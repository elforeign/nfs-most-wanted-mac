#!/usr/bin/env python3
"""Validate per-second slowdown evidence in a normal session's perf.tsv.

Proof: slowlog_test.py: complete X360 wait capture PASS (exit 0); removing
each of 20 required columns FAIL (20/20, exit 1); zeroed X360 wait hook
FAIL (exit 1). PC zero waits PASS; X360 zero waits never count as evidence.
No game/assets required for the fixture proof. A real session is checked with:
  $TOOLS_PY -I runtime/slowlog_check.py SESSION/perf.tsv
"""
import csv
import math
from pathlib import Path
import sys

REQUIRED = ("slowlog_enabled slowlog_sample slowlog_dt slowlog_age_ms post_path "
            "lum_wait_sum_ms lum_wait_max_ms lum_wait_count lum_wait_dropped "
            "power_source battery_pct thermal low_power gpu_dev_util gpu_self_ms "
            "gpu_other_ms gpu_top gpu_mhz gpu_pstates gpu_clock_status").split()


def check(path):
    with Path(path).open() as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        missing = set(REQUIRED) - set(reader.fieldnames or [])
        if missing:
            raise ValueError('missing columns: ' + ', '.join(sorted(missing)))
        rows = list(reader)
    if not rows:
        raise ValueError('no perf rows')
    active = []
    samples = set()
    for r in rows:
        if None in r or any(r[k] is None or r[k] == '' for k in REQUIRED):
            raise ValueError('incomplete perf row')
        if r['slowlog_enabled'] != '1':
            raise ValueError('slowlog is disabled')
        if r['slowlog_sample'] == '0':
            continue  # explicit startup/stale sample, never evidence
        def number(key):
            value = float(r[key])
            if not math.isfinite(value):
                raise ValueError('nonfinite ' + key)
            return value
        if number('slowlog_dt') <= 0 or not 0 <= number('slowlog_age_ms') < 3000:
            raise ValueError('invalid sample interval/age')
        samples.add(r['slowlog_sample'])
        total, maximum, count = (number(k) for k in ('lum_wait_sum_ms', 'lum_wait_max_ms', 'lum_wait_count'))
        if not 0 <= maximum <= total or count < 0 or number('lum_wait_dropped') != 0:
            raise ValueError('invalid or dropped luminance waits')
        # post_path is current; waits may come from X360 before a mid-interval switch.
        if r['post_path'] == 'X360':
            active.append((total, maximum, count))
        elif r['post_path'] != 'PC':
            raise ValueError('unknown post path')
        if r['battery_pct'] != '-' and not 0 <= number('battery_pct') <= 100:
            raise ValueError('invalid battery charge')
        if r['power_source'] not in ('AC', 'battery', 'UPS', 'unknown'):
            raise ValueError('invalid power source')
        if r['thermal'] not in ('-1', '0', '1', '2', '3') or r['low_power'] not in ('-1', '0', '1'):
            raise ValueError('invalid thermal/low power state')
        if r['gpu_mhz'] == '-' and r['gpu_pstates'] == '-' and not r['gpu_clock_status'].startswith(('not_decoded_yet:', 'baseline')):
            raise ValueError('GPU clock unavailable without a reason')
    if not samples:
        raise ValueError('no fresh background samples')
    if active and not any(total > 0 and maximum > 0 and count > 0 for total, maximum, count in active):
        raise ValueError('X360 luminance wait stuck at zero')
    waits_present = any(float(r['lum_wait_sum_ms']) > 0 for r in rows)
    return f'PASS slowlog: {len(rows)} rows, {len(samples)} samples, {len(active)} X360 rows; luminance wait evidence present' if active else f'PASS slowlog: {len(rows)} PC rows, ' + ('boundary luminance waits retained' if waits_present else 'zero luminance waits')


def main():
    try:
        print(check(sys.argv[1]))
    except (IndexError, OSError, ValueError) as e:
        print('FAIL slowlog: ' + str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
