#!/usr/bin/env python3
"""Career Max Performance prompt row vs the other CustomizeCategory.fng elements (no assets, no game).
Geometry is pinned from the decoded package (PC and console builds carry the same one; every group is placed in the
same 640x480 screen space, centre origin, y down, so the relation holds at any resolution or aspect): group origin,
child offset and size per prompt, and the Cash/Total box (root, background, corner brackets). The row heights come
from the stripe images (console 246.9x30, keyboard 193x26). The row y values are read from runtime/maxperf_career.c.
Checks: neither Max Performance row intersects Showcase (console or keyboard), Done or the Cash/Total box, and both
end above the box. Control: the retail row positions (console 156, keyboard 156) must fail.
Old code (console row 156, keyboard row retail 156): 5 failures, 12,513 sq units overlap (console row on Showcase 6,859; keyboard
row on console Showcase 2,346 and on Cash/Total 3,308); fixed (rows 30 and 53): 0 failures, 0 overlap, 5 / 6 units clear. Reproduce the old numbers with --source <old file>.
"""
from pathlib import Path
import argparse
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

# Max Performance groups: origin x (y comes from the code) and (centre x, centre y, width, height) of stripe and icon.
CONSOLE_MAXPERF = {'origin': (-222.0, None), 'kids': [(51.0, 23.0, 246.92, 30.0), (-56.0, 23.0, 32.0, 32.0)]}
KEYBOARD_MAXPERF = {'origin': (-241.0, None), 'kids': [(43.0, 0.0, 193.0, 26.0), (-40.0, -1.0, 32.0, 32.0)]}
# Bounding boxes (x0, y0, x1, y1) of the other elements, each the union of its stripe, icon and (for Cash/Total) background
# and corner brackets, from the decoded group origins (Showcase console -240.3,174.7; Showcase keyboard -142,215;
# Done -242,214; Cash/Total root -178,125) and child offsets.
FIXED = {
    'Showcase (console)': (-297.0, 159.0, -49.5, 191.0),
    'Showcase (keyboard)': (-195.0, 198.0, -0.5, 230.0),
    'Done': (-296.0, 198.0, -198.0, 230.0),
    'Cash/Total': (-296.1, 74.0, -49.1, 156.0),
}


def rects(group, y):
    ox, oy = group['origin']
    oy = y if oy is None else oy
    return [(ox+dx-w/2, oy+dy-h/2, ox+dx+w/2, oy+dy+h/2) for dx, dy, w, h in group['kids']]


def box(rs):
    return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))


def overlap(a, b):
    w = min(a[2], b[2])-max(a[0], b[0])
    h = min(a[3], b[3])-max(a[1], b[1])
    return w*h if w > 0 and h > 0 else 0.0


def row_values(text):
    pad = re.search(r'#define MAXPERF_PAD_Y ([\d.]+)f', text)
    pc = re.search(r'#define MAXPERF_PC_Y ([\d.]+)f', text)
    if pad and pc:
        return float(pad.group(1)), float(pc.group(1))
    old = re.search(r'\(show \? ([\d.]+)f : ([\d.]+)f\)', text)   # frozen layout: only the console row moved, keyboard stayed at 156
    assert old, 'no row positions found'
    return float(old.group(1)), 156.0


def evaluate(pad_y, pc_y):
    others = FIXED
    total, failures = 0.0, []
    for label, group, y in (('console Max Performance', CONSOLE_MAXPERF, pad_y), ('keyboard Max Performance', KEYBOARD_MAXPERF, pc_y)):
        row = box(rects(group, y))
        for name, other in others.items():
            area = overlap(row, other)
            total += area
            if area:
                failures.append('%s overlaps %s by %.0f' % (label, name, area))
        gap = others['Cash/Total'][1]-row[3]
        if gap < 0:
            failures.append('%s ends %.1f below the top of Cash/Total' % (label, -gap))
        print('%s: y %.1f..%.1f, %.1f clear above Cash/Total' % (label, row[1], row[3], gap))
    return total, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT/'runtime/maxperf_career.c')
    args = parser.parse_args()
    pad_y, pc_y = row_values(args.source.read_text())
    total, failures = evaluate(pad_y, pc_y)
    print('rows: console y=%g keyboard y=%g, overlap area %.0f' % (pad_y, pc_y, total))
    for f in failures:
        print('FAIL', f)
    if args.source == ROOT/'runtime/maxperf_career.c':
        control, control_failures = evaluate(156.0, 156.0)
        assert control_failures and control > 0, 'control (retail rows) must fail'
        print('control (retail rows 156/156): %d failures, overlap area %.0f' % (len(control_failures), control))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
