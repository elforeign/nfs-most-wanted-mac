#!/usr/bin/env python3
# refs_compare.py - the reference-frame comparison behind runtime/refs.sh (see runtime/README.md, "Reference frames").
#
#   refs_compare.py check <ref dir> <new dir> <diff dir>   compare every ref against the new capture of the same name
#   refs_compare.py accept <new dir> <ref dir>             store the new captures as the references (JPEG <= 200 KB)
#   refs_compare.py noise <dir> <dir> [<dir>...]           the measure between runs of the same build (all pairs)
#
# Both pictures are reduced by BLOCK x BLOCK box averaging first, so what the game draws differently from run to run
# (rain and lens drops, particles, the title prompt's pulse) and the references' JPEG compression average out; a
# block counts as changed when its mean differs from the reference's by more than BLOCK_TOL in any colour channel
# (0-255). A frame fails when more than its MAX_BLOCKS percent of its blocks changed. Run python with -I.
import os
import sys

import numpy as np
from PIL import Image

NAMES = ["chase", "hood", "rain", "title", "menu"]
BLOCK = 8
BLOCK_TOL = 12.0
# Percent of blocks allowed to change, per frame: at least twice the worst run-to-run noise measured on the single-launch
# capture (refs.sh noise 3, 2026-10-20: chase 0.4, title 1.5, menu 1.5; rain streaks and lens drops are the race frames'
# noise, the title prompt's pulse the title's). hood (2026-10-21, NFS_LOCKSTEP): 0.24-0.55 % run to run and 0.26-0.95 %
# against the reference in 6 solo captures, one of them with a 60 ms frame at sim 20.7 s (BOOT_DIAG_HITCH=20.7:60), which
# without the lockstep clock gave 9.7 % (gate.KlTdlx's 6.4 % failure); the mirror with NFS_MIRROR_FOG=off changes 4.3 %
# of the frame (73 % of the mirror's blocks), which passed the old 6 % limit and fails this one.
MAX_BLOCKS = {"chase": 3.0, "hood": 5.0, "rain": 4.0, "title": 4.0, "menu": 4.0}
# The rain frame looks at the road only (rows 340-470: the wet-road reflection and the X360 rain ripples, which the other
# frames' 8 px / 12 level blocks barely see) through coarser, more sensitive blocks (16 px, 4 levels): the lens drops and
# streaks that move from run to run are small and sparse, so they average out there, while a missing reflection or PC
# ripples shift whole road blocks (numbers: runtime/refs.sh header). The hood frame looks at the mirror glass only (x 488-792,
# y 40-128: what it is there for): the lens drops and streaks elsewhere in the hood view land differently every run and
# took it past its limit (2.08 / 2.23 %), so the rain there is ignored.
# name -> ((rows), (columns) or None, block, tolerance); others use the defaults.
REGION = {"rain": ((340, 470), None, 16, 4.0), "hood": ((40, 128), (488, 792), BLOCK, BLOCK_TOL)}
MAX_BYTES = 200 * 1024


def load(path):
    return np.asarray(Image.open(path).convert("RGB")).astype(np.float32)


def blocks(a, block=BLOCK):
    h, w = a.shape[0] // block * block, a.shape[1] // block * block
    return a[:h, :w].reshape(h // block, block, w // block, block, 3).mean(axis=(1, 3))


def measure(ref, new, name=""):
    """(percent of blocks changed, largest block difference, mean block difference) or None when the sizes differ."""
    if ref.shape != new.shape:
        return None
    rows, cols, block, tol = REGION.get(name, (None, None, BLOCK, BLOCK_TOL))
    if rows:
        ref, new = ref[rows[0]:rows[1]], new[rows[0]:rows[1]]
    if cols:
        ref, new = ref[:, cols[0]:cols[1]], new[:, cols[0]:cols[1]]
    d = np.abs(blocks(ref, block) - blocks(new, block)).max(axis=2)
    return 100.0 * float((d > tol).mean()), float(d.max()), float(d.mean())


def find(d, name):
    for ext in ("jpg", "png"):
        p = os.path.join(d, name + "." + ext)
        if os.path.exists(p):
            return p
    return None


def diff_image(ref, new, path):
    d = np.abs(ref - new).max(axis=2)
    heat = np.clip(d * 4, 0, 255).astype(np.uint8)
    third = lambda a: Image.fromarray(a.astype(np.uint8)).resize((640, int(640 * a.shape[0] / a.shape[1])))
    row = [third(ref), third(new), third(np.stack([heat, heat // 3, heat // 3], axis=2))]
    out = Image.new("RGB", (1920, row[0].height))
    for i, im in enumerate(row):
        out.paste(im, (640 * i, 0))
    out.save(path, quality=85)


def check(refdir, newdir, diffdir):
    bad = []
    lines = []
    for name in NAMES:
        rp, np_ = find(refdir, name), find(newdir, name)
        if not rp:
            bad.append("%s: no reference (supply reference frames)" % name)
            continue
        if not np_:
            bad.append("%s: not captured" % name)
            continue
        try:
            ref, new = load(rp), load(np_)
        except (OSError, ValueError) as e:
            bad.append("%s: cannot decode image: %s" % (name, e))
            continue
        m = measure(ref, new, name)
        if m is None:
            bad.append("%s: size %dx%d, reference %dx%d" % (name, new.shape[1], new.shape[0], ref.shape[1], ref.shape[0]))
            continue
        lines.append("%-6s %5.2f %% of blocks changed (limit %.1f), largest block diff %5.1f, mean %.2f" % (name, m[0], MAX_BLOCKS[name], m[1], m[2]))
        if m[0] > MAX_BLOCKS[name]:
            os.makedirs(diffdir, exist_ok=True)
            p = os.path.join(diffdir, "refs_diff_%s.jpg" % name)
            diff_image(ref, new, p)
            bad.append("%s: %.2f %% of blocks changed (limit %.1f), diff image %s (reference | new | difference x4)" % (name, m[0], MAX_BLOCKS[name], p))
    print("\n".join(lines))
    for b in bad:
        print("REFS FAIL: " + b)
    return 1 if bad else 0


def accept(newdir, refdir):
    os.makedirs(refdir, exist_ok=True)
    for name in NAMES:
        p = find(newdir, name)
        if not p:
            print("REFS: %s not captured" % name)
            return 1
        im = Image.open(p).convert("RGB")
        for q in (90, 85, 80, 75, 70, 60):
            out = os.path.join(refdir, name + ".jpg")
            im.save(out, quality=q, optimize=True)
            if os.path.getsize(out) <= MAX_BYTES:
                break
        for ext in ("png",):
            old = os.path.join(refdir, name + "." + ext)
            if os.path.exists(old):
                os.remove(old)
        print("accepted %s: %dx%d JPEG q%d, %d KB" % (name, im.width, im.height, q, os.path.getsize(out) // 1024))
    return 0


def noise(dirs):
    for name in NAMES:
        ims = [load(find(d, name)) for d in dirs if find(d, name)]
        for i in range(len(ims)):
            for j in range(i + 1, len(ims)):
                m = measure(ims[i], ims[j], name)
                print("%-6s run %d vs %d: %s" % (name, i + 1, j + 1, "size differs" if m is None else
                      "%5.2f %% of blocks changed, largest block diff %5.1f, mean %.2f" % m))
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "check":
        sys.exit(check(*sys.argv[2:5]))
    if len(sys.argv) == 4 and sys.argv[1] == "accept":
        sys.exit(accept(*sys.argv[2:4]))
    if len(sys.argv) >= 4 and sys.argv[1] == "noise":
        sys.exit(noise(sys.argv[2:]))
    print(__doc__)
    sys.exit(2)
