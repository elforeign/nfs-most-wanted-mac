#!/bin/sh
# carshadow_check.sh <scratch-dir> [--counts]: the car's ground shadow (CARSHADOW, CarRenderInfo::DrawAmbientShadow
# 00743EA0) under the soft-particle depth fade (Particles Physical + soft smoke, decal_depth.m / render.m).
# One guarded race (15.1.1, no opponents, autodrive, lockstep 60 Hz, 2560x1440, far chase camera from sim 4) with
# X360 shadow fit, Particles Physical, soft smoke On and the speed-reactive chase camera.
# At sim 16 the held look variant `softoff` redraws the same frame without the soft-particle path. It measures the mean luminance of the road strip
# just below the rear bumper (x1110-1440 y1065-1100) and of a road patch away from the car (x1000-1100 y1200-1300).
# PASS: the production frame's strip is within 3 levels of `softoff` (the fade leaves the ground shadow alone).
# Control: `softoff`'s strip must be >= 15 levels darker than the road patch (the shadow is in the box), else FAIL.
# Measured (cc-carshadow): before the fix strip 49.4 vs softoff 32.1 (FAIL, 17.3 apart), road 64.8; after 32.2 vs 32.1
# (PASS). The retained default shadow fit is covered by the same pixel check.
# --counts <scratch-dir> re-measures an existing run and never launches a game.
set -u
cd "$(dirname "$0")/.."
. runtime/env.sh
nfs_need_py
launch=1
if [ "${1:-}" = --counts ]; then launch=0; shift; fi
out=${1:?scratch-dir}
last=simshot_016.00_softoff.png
if [ "$launch" = 1 ]; then
    mkdir -p "$out"
    rm -rf "$out/race" "$out/race.user"
    runtime/run_guarded.sh 120 "$out/race" NFS_SIMHZ=60 NFS_LOCKSTEP=1 NFS_DISPLAY_PIXELS=2560x1440 \
        BOOT_RACE=15.1.1 BOOT_RACE_AI=0 BOOT_AUTODRIVE=1 BOOT_KEYS=seq:sim:4,C BOOT_DIAG_SIMSHOTS=16 \
        BOOT_DIAG_LOOK_AB=softoff NFS_SHADOW_FIT=x360 NFS_PARTICLES=physical \
        NFS_SOFT_SMOKE=on NFS_CHASE_CAM=on > "$out/race.launch" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null && [ ! -s "$out/race/$last" ]; do sleep 2; done
    sleep 3; kill -TERM "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
fi
"$TOOLS_PY" -I - "$out/race" <<'EOF'
import os, sys
import numpy as np
from PIL import Image
d = sys.argv[1]
def lum(name):
    p = os.path.join(d, name)
    if not os.path.exists(p): return None
    a = np.asarray(Image.open(p).convert('RGB'), float).mean(2)
    return a[1065:1100, 1110:1440].mean(), a[1200:1300, 1000:1100].mean()
base, off = lum('simshot_016.00.png'), lum('simshot_016.00_softoff.png')
fails = []
if base is None or off is None:
    fails.append('missing simshots')
else:
    print(f'strip production {base[0]:.1f} softoff {off[0]:.1f} road {off[1]:.1f}')
    if off[1] - off[0] < 15: fails.append('control: no ground shadow in the box without the fade (wrong spot?)')
    if abs(base[0] - off[0]) > 3: fails.append(f'soft fade changes the ground shadow by {abs(base[0] - off[0]):.1f} levels')
print('carshadow_check: ' + ('PASS' if not fails else 'FAIL: ' + '; '.join(fails)))
sys.exit(1 if fails else 0)
EOF
