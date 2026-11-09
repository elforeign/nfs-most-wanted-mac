#!/bin/sh
# live_fake.sh <scratch-dir> [secs] [NAME=value ...]: live streaming to a loopback fake HLS ingest across focus losses.
# One guarded front-end run (default 330 s) with NFS_TEST_LIVE_FAKE (live_fake.m: the poster's destination becomes
# http://127.0.0.1:<port>/http_upload_hls of runtime/live_fake_ingest.py; nothing reaches YouTube) and the clip host-time
# fallback (guarded windows get no presented frames). Script: skip movies, `livego`, then three focus losses as the
# player checks the stream in another app: unfocus:15 (Cmd-Tab: the game parks in GetMessageA, no frame is presented),
# hide:15 (Cmd-H: off screen as well), unfocus:30.
# PASS: no "live: error" in session.txt, the first segment accepted, every segment and playlist valid, at least 80 % of
# the 2 s segments the live span holds, no gap over 6 s between segment arrivals, all three focus losses ran.
# Measured (cc-livefix3, solo): fixed PASS, 151 segments + 151 playlists over 302 s, max gap 2.1 s, focus losses 3/3;
# control NFS_TEST_LIVE_HOLD=0 (no held frames, the reported behaviour) FAIL "error kind=encoder reason=no-idr-5s" at the
# first unfocus (33 segments over 64 s). Before held frames extended the sound target: FAIL reason=queue-full (32 over 62 s).
# --counts <scratch-dir> re-judges an existing run and never launches a game.
set -u
cd "$(dirname "$0")/.."
. runtime/env.sh
nfs_need_py
launch=1
if [ "${1:-}" = --counts ]; then launch=0; shift; fi
out=${1:?scratch-dir}; shift
secs=330
case "${1:-}" in [0-9]*) secs=$1; shift;; esac
if [ "$launch" = 1 ]; then
    rm -rf "$out/game" "$out/game.user" "$out/ingest"; mkdir -p "$out/ingest"
    "$TOOLS_PY" -I runtime/live_fake_ingest.py "$out/ingest" > "$out/ingest.out" 2>&1 &
    ingest=$!
    n=0; while [ ! -s "$out/ingest/port" ] && [ $n -lt 50 ]; do sleep 0.2; n=$((n + 1)); done
    [ -s "$out/ingest/port" ] || { kill "$ingest" 2>/dev/null; echo "LIVEFAKE FAIL: fake ingest did not start"; exit 1; }
    port=$(cat "$out/ingest/port")
    runtime/run_guarded.sh "$secs" "$out/game" NFS_TEST_LIVE_FAKE="$port" NFS_TEST_CLIP_HOSTTIME=1 \
        BOOT_KEYS=seq:skipmovies,wait:600,livego,wait:4000,unfocus:15,wait:4000,hide:15,wait:4000,unfocus:30 \
        "$@" > "$out/game.launch" 2>&1
    kill -TERM "$ingest" 2>/dev/null; wait "$ingest" 2>/dev/null
fi
"$TOOLS_PY" -I - "$out" <<'PY'
import glob, os, sys
out = sys.argv[1]
fails = []
rows = []
try:
    for line in open(os.path.join(out, 'ingest', 'ingest.tsv')):
        t, name, size, ok, extinf = line.rstrip('\n').split('\t')
        rows.append((float(t), name, int(size), ok == '1', float(extinf)))
except OSError:
    fails.append('no ingest log')
segments = [r for r in rows if r[1].endswith('.ts')]
playlists = [r for r in rows if r[1].endswith('.m3u8')]
sessions = sorted(glob.glob(os.path.join(out, 'game.user', '**', 'session.txt'), recursive=True))
session = open(sessions[-1], errors='replace').read() if sessions else ''
events = ''
try: events = open(os.path.join(out, 'game', 'events.log'), errors='replace').read()
except OSError: pass
errors = [l.strip() for l in session.splitlines() if 'live: error' in l]
losses = sum(events.count('script: ' + s) for s in ('unfocus:15', 'hide:15', 'unfocus:30'))
span = segments[-1][0] - segments[0][0] if len(segments) > 1 else 0.0
gaps = [b[0] - a[0] for a, b in zip(segments, segments[1:])]
gap = max(gaps) if gaps else 0.0
expected = span / 2.0
invalid = sum(1 for r in rows if not r[3])
extinf = max((r[4] for r in playlists), default=0.0)
if not sessions: fails.append('no session.txt')
if errors: fails.append(errors[0].split('live: ', 1)[-1])
if 'live: first segment accepted code=202' not in session: fails.append('first segment not accepted')
if invalid: fails.append(f'{invalid} invalid uploads')
if len(segments) < 10 or len(segments) - 1 < 0.8 * expected:
    fails.append(f'{len(segments)} segments over {span:.0f} s (expected ~{expected:.0f})')
if gap > 6.0: fails.append(f'segment gap {gap:.1f} s')
if losses < 3: fails.append(f'{losses}/3 focus losses ran')
crash = [f for f in os.listdir(os.path.join(out, 'game')) if f.startswith(('crash', 'hang_', 'stall_'))] if os.path.isdir(os.path.join(out, 'game')) else []
if crash: fails.append('report ' + ' '.join(crash[:3]))
summary = (f'{len(segments)} segments + {len(playlists)} playlists over {span:.0f} s, max gap {gap:.1f} s, '
           f'max EXTINF {extinf:.2f} s, invalid {invalid}, focus losses {losses}/3')
print('LIVEFAKE ' + ('PASS: ' + summary if not fails else 'FAIL: ' + '; '.join(fails) + ' (' + summary + ')'))
sys.exit(1 if fails else 0)
PY
