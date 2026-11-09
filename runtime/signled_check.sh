#!/bin/sh
# signled_check.sh <scratch-dir> [--counts]: built-in glow placement at the Bank of Rockport sign.
# Two guarded runs at the sign: still and creeping 7 cm (throttle from sim 21).
# Counts LED-blue px in the sign box and the separate strip above it.
# PASS: box <= 1000 in every frame; still strip within 5% of the measured retail reference.
# Reference (16 frames): retail box 5117-5269, strip 210732; fixed still box 631-653,
# moving box 683-695, still strip 207280. The retail reference exceeds the box limit.
# Synthetic recount: 0 failures for the valid fixture; bleed (2 limits) and missing strip (1 limit) fail.
# --counts <scratch-dir> recounts existing captures without launching.
set -u
cd "$(dirname "$0")/.."
. runtime/env.sh
nfs_need_py
launch=1
if [ "${1:-}" = --counts ]; then launch=0; shift; fi
out=${1:?scratch-dir}
keys='seq:skipmovies,wait:450,RETURN,wait:400,RETURN,wait:400,RETURN,wait:300,RETURN,wait:300,LEFT,wait:60,RETURN,wait:300,RETURN,wait:300,RETURN,wait:300,BACK,BACK,BACK,BACK,T,E,S,T,RETURN,wait:300,LEFT,wait:60,RETURN,wait:400,RETURN,wait:400,RETURN,wait:400,RETURN,wait:400,RETURN,wait:400,LEFT,wait:60,RETURN,wait:400,LEFT,wait:60,RETURN'
shots=21.996,22.013,22.029,22.046,22.063,22.079,22.096,22.113,22.129,22.146,22.163,22.179,22.196,22.213,22.229,22.246
last=simshot_022.25.png
run() {   # name, extra keys: one guarded run, stopped once its last simshot is written
    rm -rf "$out/$1" "$out/$1.user"
    runtime/run_guarded.sh 400 "$out/$1" "BOOT_KEYS=$keys$2" BOOT_PAD=sim BOOT_SKIPINTRO=1 \
        "BOOT_DIAG_WARP=20:-330.2,99.83,1456.6:dir=-0.51,0,-0.86" NFS_LOCKSTEP=1 NFS_SIMHZ=60 \
        BOOT_DIAG_SIMSHOTS=$shots > "$out/$1.launch" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null && [ ! -s "$out/$1/$last" ]; do sleep 2; done
    sleep 3; kill -TERM "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
}
if [ "$launch" = 1 ]; then
    mkdir -p "$out"
    run on ''
    run move ',sim:21,padtriggers:0/0.35:60'
fi
"$TOOLS_PY" -I - "$out" <<'EOF'
import glob, sys
import numpy as np
from PIL import Image
out = sys.argv[1]
def counts(name):
    rows = []
    for f in sorted(glob.glob(f'{out}/{name}/simshot_*.png')):
        a = np.asarray(Image.open(f).convert('RGB')).astype(int)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        blue = (b > 140) & (b > r + 70) & (b > g + 10)
        rows.append((f.split('_')[-1][:-4], int(blue[680:810, 2420:2820].sum()), int(blue[522:680, 2300:3500].sum())))
    return rows
c = {n: counts(n) for n in ('on', 'move')}
for n, rows in c.items():
    print(n, ' '.join(f'{t}:{box}/{strip}' for t, box, strip in rows))
fails = []
if len(c['on']) < 16 or len(c['move']) < 16: fails.append('missing simshots')
if c['on'] and max(r[1] for r in c['on']) > 1000: fails.append('On still box > 1000')
if c['move'] and max(r[1] for r in c['move']) > 1000: fails.append('On moving box > 1000')
so = 210732 * len(c['on']) / 16; sn = sum(r[2] for r in c['on'])
print(f'strip reference {so:g} built-in {sn}')
if not so or abs(sn - so) > 0.05 * so: fails.append('LED strip above the sign changed > 5 %')
for n in ('on', 'move'):
    if c[n]: print(f'{n} box {min(r[1] for r in c[n])}-{max(r[1] for r in c[n])}')
print('signled_check: ' + ('PASS' if not fails else 'FAIL: ' + '; '.join(fails)))
sys.exit(1 if fails else 0)
EOF
