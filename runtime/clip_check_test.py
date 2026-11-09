#!/usr/bin/env python3
"""clip_check_test.py - pins runtime/clip_check.py's verdict on avmediainfo output of a real recorder clip.

The fixture is avmediainfo's text for a clip the guarded smoke recorded (runtime/clip_smoke.sh, HEVC + AAC, 14.639 s);
each control breaks one property the smoke must catch: audio track removed, 44.1 kHz, mono, H.264 accepted,
audio track 50 ms late, silent audio, duration two frames off the recorder's span, duration outside the window.
Proof: 36/36 checks pass (AAC_DELAY = 0: 35/36, the AAC-delay control fails). New duration controls: original catches 0/6, fixed 6/6 (short, long and missing
video/audio durations). New shell controls with fake runner/media: original catches 0/10, fixed 10/10
(three linked-home paths, two home overrides, saved-with-error, game exits 134/7/143, missing exit record).
Normal exit and a requested stop of a real fake child both pass. No game or system process-name termination is used.
Replay: one export passes; two exports fail (removing the count guard passes).
29.900 s fails the strict 30-second window; restoring recorder slack passes.
"""
import hashlib, os, pathlib, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clip_check
from clip_check import judge

FIXTURE = """Asset: clip.mp4
Duration: 14.639 seconds (702672/48000)
Track count: 2
Track 1: Video 'vide'
	Enabled: Yes
	Format Description 1:
		Format: HEVC 'hvc1'
		Dimensions: 2292 x 1440
		Encoded Pixels: 2292 x 1440
		Presentation Dimensions: 2292 x 1440
	System support for decoding this track: Yes
	Data size: 29269259 bytes
	Media time scale: 60000
	Duration: 14.639 seconds
	Estimated data rate: 15995.205 kbit/s
	Nominal frame rate: 56.698 fps
	Minimum sample duration: 995/60000 seconds
	1 segment present
	Index   Media Start  Media Duration   Track Start  Track Duration 
	    1  00:00:00.000    00:00:14.639  00:00:00.000    00:00:14.639
Track 2: Sound 'soun'
	Enabled: Yes
	Format Description 1:
		Format: MPEG-4 AAC 'aac '
		Channel Layout: Stereo (L R)
		Sample rate: 48000.0
		Bytes per packet: 0
		Frames per packet: 1024
		Bytes per frame: 0
		Channels per frame: 2
		Bits per channel: 0
	System support for decoding this track: Yes
	Data size: 347180 bytes
	Media time scale: 48000
	Duration: 14.639 seconds
	Estimated data rate: 188.959 kbit/s
	1 segment present
	Index   Media Start  Media Duration   Track Start  Track Duration 
	    1  00:00:00.044    00:00:14.639  00:00:00.000    00:00:14.639

Movie analyzed with 0 error.
"""


def check(name, text, rms=0.0924, recorded=14.639, scripted=15, want=True, reason=None):
    ok, bad, _ = judge(text, rms, recorded, scripted)
    good = ok == want and (reason is None or any(reason in b for b in bad))
    print(f"{'ok  ' if good else 'FAIL'} {name}: {'pass' if ok else '; '.join(bad)}")
    return good


sound = FIXTURE.index('Track 2: Sound')
results = []
results.append(check('recorded clip', FIXTURE))
results.append(check('audio track removed', FIXTURE[:sound], want=False, reason='0 audio tracks'))
results.append(check('44.1 kHz audio', FIXTURE.replace('Sample rate: 48000.0', 'Sample rate: 44100.0'), want=False, reason='audio rate'))
results.append(check('mono audio', FIXTURE.replace('Channels per frame: 2', 'Channels per frame: 1'), want=False, reason='audio channels'))
results.append(check('H.264 video', FIXTURE.replace("Format: HEVC 'hvc1'", "Format: H.264 'avc1'")))
results.append(check('audio 50 ms late', FIXTURE[:sound] + FIXTURE[sound:].replace('00:00:14.639  00:00:00.000', '00:00:14.639  00:00:00.050'),
               want=False, reason='track starts'))
results.append(check('silent audio', FIXTURE, rms=0.0, want=False, reason='audio RMS'))
results.append(check('duration 2 frames off the span', FIXTURE, recorded=14.639 + 2 / 60, want=False, reason='vs recorded'))
def audio_span(secs):   # the sound track's own duration lines set to secs
    return FIXTURE[:sound] + FIXTURE[sound:].replace('14.639', f'{secs:.3f}')
# AAC encoder delay (2112 samples, 44.0 ms) shortens the sound track only: the train-0959 clip read audio 14.543 s vs
# video 14.587 s. Without the allowance the first control fails (as that clip did); the other two must still fail.
results.append(check('audio short by the AAC delay', audio_span(14.639 - 0.044)))
results.append(check('audio short by delay + 2 frames', audio_span(14.639 - 0.044 - 2 / 60), want=False, reason='Sound duration'))
results.append(check('audio long by the AAC delay', audio_span(14.639 + 0.044), want=False, reason='Sound duration'))
results.append(check('outside the 15 s window', FIXTURE, scripted=16, want=False, reason='vs scripted'))

# Pinned original fragments: controls exercise the predecessor without reading git history.
OLD_JUDGE = r'''def judge(text, rms, recorded, scripted):
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
        if abs(dur - scripted) > WINDOW_SLACK: bad.append(f'duration {dur:.3f} s vs scripted {scripted:.1f} s')
    v = video[0]['format'] if video else '-'
    summary = f"{dur} s {v} + {len(audio)} audio" + (f" (RMS {rms:.4f})" if rms is not None else "")
    return not bad, bad, summary

'''
OLD_JUDGE_SHA = '9dae7bb10cdde3b63be1f9d9a90a42c24f98b418034ffbe8e2e6a1c6bcf6e278'
OLD_SMOKE = r'''out=${1:?scratch dir}; shift
mkdir -p "$out" && out=$(cd "$out" && pwd -P)
home=$out/home
real=$(cd "$HOME" && pwd -P)
case $home in "$real"|"$real/Movies"*) echo "CLIPSMOKE FAIL: scratch home $home would be the real home folder"; exit 2;; esac
mkdir -p "$home"
movies_before=$(ls -la "$real/Movies/NFSMW" 2>&1)
name=clip; secs=70; window=15
rm -rf "$out/$name" "$out/$name.out" "$out/$name.user" "$home/Movies/NFSMW"
runtime/run_guarded.sh "$secs" "$out/$name" BOOT_RACE=15.1.1 BOOT_AUTODRIVE=1 NFS_AUDIO_FAKE_DEVICE=1 \
    NFS_TEST_CLIP_HOSTTIME=1 CFFIXED_USER_HOME="$home" \
    BOOT_KEYS=seq:sim:10,cliprec,wait:$((window * 60)),cliprec "$@" > /dev/null 2>&1 &
rg=$!
while kill -0 $rg 2>/dev/null && [ ! -e "$out/$name.out" ]; do sleep 0.5; done   # waiting for a game slot
i=0   # the recorder prints one `cliprec:` line when the clip is saved or abandoned
while ! grep -q '^cliprec: ' "$out/$name.out" 2>/dev/null && [ $i -lt $((secs * 2)) ] && kill -0 $rg 2>/dev/null; do sleep 0.5; i=$((i + 1)); done
sleep 1
pkill -TERM -P $rg 2>/dev/null
wait $rg 2>/dev/null
fail() { echo "CLIPSMOKE FAIL: $*"; exit 1; }
[ "$(ls -la "$real/Movies/NFSMW" 2>&1)" = "$movies_before" ] || fail "the real Movies/NFSMW folder changed"
ls "$out/$name" 2>/dev/null | grep -q -i -E "crash|hang_|stall_" && fail "report $(ls "$out/$name" | grep -i -E 'crash|hang_|stall_' | head -3 | tr '\n' ' ')"
line=$(grep '^cliprec: ' "$out/$name.out" | head -1)
[ -n "$line" ] || fail "no cliprec line (recorder never finished; $(grep -c 'script: cliprec' "$out/$name/events.log" 2>/dev/null) cliprec steps ran)"
case $line in "cliprec: saved "*) ;; *) fail "$line";; esac
clip=$(ls "$home"/Movies/NFSMW/Clip-*.mp4 2>/dev/null | head -1)
[ -n "$clip" ] || fail "clip not under the scratch home ($line)"
recorded=$(echo "$line" | sed -n 's/.* video \([0-9.]*\) s,.*/\1/p')
r=$("$py" -I runtime/clip_check.py "$clip" "$recorded" "$window")
case $r in "clip PASS"*) echo "CLIPSMOKE PASS: ${r#clip PASS: }; ${line#cliprec: saved * video }";; *) fail "${r#clip FAIL: }; $line";; esac
'''
OLD_SMOKE_SHA = 'e0e6ce240afd2b5e05f0e4ad815b4cf0bfdd6eca3be40f01bada236d6338c9c0'

assert hashlib.sha256(OLD_JUDGE.encode()).hexdigest() == OLD_JUDGE_SHA
assert hashlib.sha256(OLD_SMOKE.encode()).hexdigest() == OLD_SMOKE_SHA
old_namespace = dict(vars(clip_check))
exec(OLD_JUDGE, old_namespace)


def track_controls():
    caught = 0
    for kind, start, end in [('Video', 0, sound), ('Sound', sound, len(FIXTURE))]:
        for duration in ('1.000', '15.639', None):
            block = FIXTURE[start:end]
            old_line = '\tDuration: 14.639 seconds\n'
            block = block.replace(old_line, '' if duration is None else f'\tDuration: {duration} seconds\n')
            text = FIXTURE[:start] + block + FIXTURE[end:]
            before = old_namespace['judge'](text, 0.0924, 14.639, 15)[0]
            after, bad, _ = judge(text, 0.0924, 14.639, 15)
            good = before and not after and any(f'{kind} duration' in b for b in bad)
            results.append(good)
            caught += good
    print(f'track duration controls: original caught 0/6; fixed caught {caught}/6')


FAKE_RUNNER = r'''#!/bin/sh
shift
out=$1; shift
home=
for a in "$@"; do
    case $a in CFFIXED_USER_HOME=*) home=${a#*=};; esac
done
mkdir -p "$out" "$home/Movies/NFSMW"
printf launched > "$out/../launched"
if [ "${FAKE_LIVE:-0}" = 1 ]; then
    /bin/sleep 5 &
    child=$!
    trap 'kill "$child" 2>/dev/null; wait "$child" 2>/dev/null' EXIT
    mkdir -p "$NFS_RUNS/slots"
    printf 'run %s shared\n' "$child" > "$NFS_RUNS/slots/$$"
    printf '%s\n' "$child" > "$FAKE_PID_FILE"
fi
printf clip > "$home/Movies/NFSMW/Clip-fake.mp4"
printf 'cliprec: saved fake video 14.639 s, audio 14.639 s%s\n' "${FAKE_ERROR:-}" > "$out.out"
if [ "${FAKE_LIVE:-0}" = 1 ]; then
    wait "$child"; code=$?
    printf 'run_guarded: exit %s (stopped by the smoke)\n' "$code"
elif [ "${FAKE_RECORD:-1}" = 1 ]; then
    printf 'run_guarded: exit %s (exited by itself)\n' "${FAKE_RC:-0}"
fi
# The wrapper exits successfully even if the game's recorded status is abnormal.
exit 0
'''


def shell_case(body, scenario, replay=False):
    with tempfile.TemporaryDirectory(prefix='clip-smoke-') as tmp:
        root = pathlib.Path(tmp)
        runtime = root / 'runtime'; runtime.mkdir()
        bindir = root / 'bin'; bindir.mkdir()
        real = root / 'real'; (real / 'Movies/NFSMW').mkdir(parents=True)
        sentinel = real / 'Movies/NFSMW/keep'; sentinel.write_text('keep')
        out = root / 'scratch'; out.mkdir()
        extras = []
        env = dict(os.environ, HOME=str(real), TOOLS_PY=sys.executable, NFS_RUNS=str(root / "runs"),
                   PATH=str(bindir) + os.pathsep + os.environ['PATH'])
        if scenario == 'home link':
            (out / 'home').symlink_to(real, target_is_directory=True)
        elif scenario == 'Movies link':
            (out / 'home').mkdir()
            (out / 'home/Movies').symlink_to(real / 'Movies', target_is_directory=True)
        elif scenario == 'ancestor link':
            (root / 'alias').symlink_to(real, target_is_directory=True)
            (out / 'home').symlink_to(root / 'alias', target_is_directory=True)
        elif scenario in ('HOME override', 'CFFIXED_USER_HOME override'):
            extras = [scenario.split()[0] + '=' + str(real)]
        elif scenario == 'saved error':
            env['FAKE_ERROR'] = '; error: Recording stopped: overloaded'
        elif scenario == 'requested stop':
            env['FAKE_LIVE'] = '1'
            env['FAKE_PID_FILE'] = str(root / 'child.pid')
        elif scenario == 'missing exit':
            env['FAKE_RECORD'] = '0'
        elif scenario.startswith('exit '):
            env['FAKE_RC'] = scenario.split()[1]
        (runtime / 'env.sh').write_text('nfs_need_py() { :; }\n')
        media="print('clip PASS: fake media')\n"
        if replay:
            media="import sys\nassert sys.argv[2:]==['30','30','--replay'],sys.argv\n"+media
        (runtime / 'clip_check.py').write_text(media)
        (runtime / 'clip_smoke.sh').write_text(body)
        fake=FAKE_RUNNER
        if replay:
            fake=fake.replace('Clip-fake.mp4','Replay-fake.mp4').replace('cliprec: saved fake video 14.639 s, audio 14.639 s','replay: saved fake video 30.000000 s, snapshot 1024 bytes')
            fake=fake.replace('mkdir -p "$out"', 'case "$*" in *BOOT_KEYS=seq:sim:5,replayarm,sim:45,replaysave*) ;; *) exit 7;; esac\nmkdir -p "$out"',1)
            if scenario=='duplicate replay':
                fake=fake.replace('printf clip >', 'printf clip > "$home/Movies/NFSMW/Replay-extra.mp4"\nprintf clip >',1)
        runner = runtime / 'run_guarded.sh'; runner.write_text(fake); runner.chmod(0o755)
        # The original fragment's process-name stop is an inert fake, never a system command.
        for name, script in [('pkill', '#!/bin/sh\nexit 1\n'),
                             ('sleep', '#!/bin/sh\nexec /bin/sleep 0.01\n'),
                             ('pgrep', '#!/bin/sh\n[ -n "${FAKE_PID_FILE:-}" ] && cat "$FAKE_PID_FILE"\n')]:
            command = bindir / name; command.write_text(script); command.chmod(0o755)
        proc = subprocess.run(['/bin/sh', str(runtime / 'clip_smoke.sh'), *(['--replay'] if replay else []), str(out), *extras],
                              env=env, capture_output=True, text=True, timeout=10)
        early = scenario.endswith('link') or scenario.endswith('override')
        good = proc.returncode != 0 and 'CLIPSMOKE FAIL:' in proc.stdout
        if early:
            good = good and sentinel.exists() and sentinel.read_text() == 'keep'
            good = good and not (out / 'launched').exists()
        if scenario in ('normal', 'requested stop'):
            good = proc.returncode == 0 and 'CLIPSMOKE PASS:' in proc.stdout
        if scenario == 'requested stop':
            record = out / 'clip.guard.out'
            good = good and record.exists() and 'run_guarded: exit 143 (' in record.read_text()
        return good, proc.stdout.strip()


def shell_controls():
    root = pathlib.Path(__file__).resolve().parent
    current = (root / 'clip_smoke.sh').read_text()
    original = '#!/bin/sh\nset -u\n. "$(dirname "$0")/env.sh"\ncd "$(dirname "$0")/.."\npy=$TOOLS_PY\n' + OLD_SMOKE
    scenarios = ('home link', 'Movies link', 'ancestor link', 'HOME override',
                 'CFFIXED_USER_HOME override', 'saved error', 'exit 134', 'exit 7', 'exit 143', 'missing exit')
    before_count = after_count = 0
    for scenario in scenarios:
        before, _ = shell_case(original, scenario)
        after, detail = shell_case(current, scenario)
        before_count += before; after_count += after
        good = not before and after
        results.append(good)
        print(f"{'ok  ' if good else 'FAIL'} shell {scenario}: {detail}")
    print(f'shell failure controls: original caught {before_count}/{len(scenarios)}; fixed caught {after_count}/{len(scenarios)}')
    for scenario in ('normal', 'requested stop'):
        normal, detail = shell_case(current, scenario)
        results.append(normal)
        print(f"{'ok  ' if normal else 'FAIL'} shell {scenario}: {detail}")

    for scenario in ('normal','duplicate replay'):
        passed,detail=shell_case(current,scenario,replay=True)
        results.append(passed)
        print(f"{'ok  ' if passed else 'FAIL'} replay shell {scenario}: {detail}")
    broken=current.replace('[ "$count" = 1 ] || fail "expected one export, found $count"', ':')
    escaped,detail=shell_case(broken,'duplicate replay',replay=True)
    results.append(not escaped)
    print(f"{'ok  ' if not escaped else 'FAIL'} duplicate-export smoke mutant: {detail}")


def replay_controls():
    text=FIXTURE.replace('14.639','30.000')
    ok,bad,_=judge(text,.0924,30,30,replay=True)
    results.append(ok)
    # Retaining the clip recorder's startup slack would accept this incorrect
    # replay even when its own recorded-span log agrees with it.
    short=text.replace('30.000','29.900')
    strict=judge(short,.0924,29.9,30,replay=True)[0]
    broken=judge(short,.0924,29.9,30,replay=False)[0]
    results.append(not strict and broken)
    delayed=text.replace('00:00:30.000  00:00:00.000','00:00:30.000  00:00:00.050')
    results.append(not judge(delayed,.0924,30,30,replay=True)[0])
    print(f'replay strict window: baseline={ok}, 29.900 s strict={strict}, removed strict mode={broken}')


track_controls()
shell_controls()
replay_controls()

print(f"clip_check_test: {sum(results)}/{len(results)} ok")
sys.exit(0 if all(results) else 1)
