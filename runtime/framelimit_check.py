"""Frame rate limit on screen: reads a guarded race run's perf.tsv and checks the loop runs at the limit.

Run (one game, the limit set by its switch), then check:
  NFS_SLOT=solo runtime/run_guarded.sh 75 <dir> BOOT_RACE=15.1.1 BOOT_AUTODRIVE=1 NFS_FRAME_LIMIT=60
  tools/.venv/bin/python -I runtime/framelimit_check.py <dir> [limit]
Race rows after the first 10 s of racing must have fps within 2 of the limit's frame rate, sim ticks per frame within
0.1 of refresh-ticks-per-frame, and >= 95 % of shown intervals in the limit's bucket (16.7 ms at 60; 25 ms at 40).
Only when the limit is longer than a refresh (60 or 40 on a 120 Hz display); otherwise it exits 3 (nothing to check).
Measured 2026-11-03, 120 Hz display, limit 60, race 15.1.1:
  before (loop at the 120 Hz tick, presentation-only limit): fps median 92.8, ticks/frame 1.29, 99.5 % at 16.7 ms
    -> FAIL (exit 1)
  after (looppace.h): fps median 60.0, ticks/frame 2.00, 99.7 % of 3448 shown intervals at 16.7 ms -> PASS
"""
import csv
import glob
import os
import statistics
import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run = sys.argv[1].rstrip('/')
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    files = sorted(glob.glob(os.path.join(run + '.user', 'sessions', '*', 'perf.tsv')))
    if not files:
        print(f'framelimit: no perf.tsv under {run}.user/sessions')
        return 3
    rows = list(csv.DictReader(open(files[-1]), delimiter='\t'))
    race = [r for r in rows if r['state'] == 'race' and r['frame_limit'] == str(limit)]
    race = race[10:]
    if len(race) < 20:
        print(f'framelimit: only {len(race)} race rows at limit {limit} after warm-up')
        return 3
    refresh = float(race[0]['refresh_ms'])
    present = float(race[0]['present_min_ms'])
    if present <= refresh * 1.01:
        print(f'framelimit: limit {limit} is not longer than a refresh ({refresh:.3f} ms): nothing to check')
        return 3
    want_fps, want_ticks = 1000 / present, present / refresh
    bucket = {16.7: 'present_16_7', 25.0: 'present_25'}.get(round(present, 1))
    fps = statistics.median(float(r['fps']) for r in race)
    ticks = statistics.median(float(r['ticks']) for r in race)
    cols = ['present_8_3', 'present_16_7', 'present_25', 'present_other']
    shown = sum(int(r[c]) for r in race for c in cols)
    hit = sum(int(r[bucket]) for r in race) if bucket else shown
    share = hit / shown if shown else 0
    ok = abs(fps - want_fps) <= 2 and abs(ticks - want_ticks) <= 0.1 and share >= 0.95
    print(f'framelimit {"PASS" if ok else "FAIL"}: limit {limit}, {len(race)} race rows: fps median {fps:.1f} '
          f'(want {want_fps:.1f}), ticks/frame {ticks:.2f} (want {want_ticks:.2f}), '
          f'{100 * share:.1f} % of {shown} shown intervals at {present:.1f} ms')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
