#!/usr/bin/env python3
"""Check post-ComputeState physics pedals.tsv from a BOOT_PAD=sim sweep.
Require 0/.25/.5/.75/1 for each pedal, simultaneous .5/.75 and 1/1,
and release, all above 8 m/s to exclude automatic braking/reverse at rest.
Each held step's final three physics ticks must match within .01.
Measured physics .25 -> .249989 passes; deliberately thresholded .25 -> 1
fails (error .75). Full forward sweep: 11/11 pairs pass, thresholded copy fails.
"""
import csv
import math
import sys
from pathlib import Path

CASES = [(0, 0), (0, .25), (0, .5), (0, .75), (0, 1),
         (.25, 0), (.5, 0), (.75, 0), (1, 0), (.5, .75), (1, 1)]


def check(rows):
    groups = {}
    for row in rows:
        r = {key: float(value) for key, value in row.items()}
        if not all(math.isfinite(v) for v in r.values()):
            raise ValueError('non-finite physics sample')
        groups.setdefault(int(r['step']), []).append(r)
    measurements = []
    for lt, rt in CASES:
        candidates = [samples for step, samples in groups.items() if step % 2 and
                      abs(samples[0]['lt'] - lt) < .001 and abs(samples[0]['rt'] - rt) < .001]
        # Last matching held step, so an initial prime cannot hide a failed repeat.
        if not candidates or len(candidates[-1]) < 3:
            raise ValueError(f'missing held physics sample {lt:.2f}/{rt:.2f}')
        samples = candidates[-1][-3:]
        for r in samples:
            if r['speed'] < 8:
                raise ValueError(f'physical {lt:.2f}/{rt:.2f}: speed {r["speed"]:.2f} < 8 m/s')
            for field, expected in [('gas', rt), ('brake', lt), ('input_gas', rt), ('input_brake', lt)]:
                if abs(r[field] - expected) > .01:
                    raise ValueError(f'physical {lt:.2f}/{rt:.2f}: {field} {r[field]:.6f}, want {expected:.2f} +/- .01')
        measurements.append((lt, rt, samples[-1]['gas'], samples[-1]['brake']))
    return measurements


def main():
    path = Path(sys.argv[1])
    if path.is_dir():
        path /= 'pedals.tsv'
    try:
        with path.open() as file:
            values = check(list(csv.DictReader(file, delimiter='\t')))
        for lt, rt, gas, brake in values:
            print(f'LT/RT {lt:.2f}/{rt:.2f} -> physics gas/brake {gas:.6f}/{brake:.6f}')
        print('PEDALS PASS (physics: linear, simultaneous, release)')
    except (OSError, ValueError, KeyError) as error:
        print(f'PEDALS FAIL: {error}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
