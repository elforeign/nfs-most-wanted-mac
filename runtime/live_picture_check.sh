#!/bin/sh
# Derived undo sweep: --sweep <new-dir>; --list / --plan do not launch;
# --sweep-logs <dir> replays saved comparisons. See runtime/docs/render-6.md.
# live_picture_check.sh <scratch-dir> [--logs] [NAME=value ...]: picture settings change mid-race without a restart.
# One guarded race (15.1.1, lockstep 60 Hz, 3440x2160, 4x MSAA) paused at sim 14; the test-only BOOT_KEYS step
# `set:<row>=<value>` toggles Contact shadows, Soft smoke, Car reflections and Full screen mode, with
# frames before and after the toggles. --logs rechecks an existing run (no launch); extra NAME=value go to the game.
# PASS needs: exactly one D3D9 CreateDevice and no restart; every IDirect3DDevice9::Reset -> 0 and 2 per queued reset;
# car reflection cube 256 (Standard) then 1024 / 11 levels (Sharp) at the creation after each set; (the default is Sharp,
# so the run goes Sharp > Standard > Sharp) contact shadows and
# soft smoke switch read back off then unset; fullscreen `mode=Native` then `mode=Borderless` lines; no hang_N/stall_N file.
# Not a pixel signal: Contact shadows, Soft smoke and Reflections do not show on the paused night frame (measured: the
# change is below the frame-to-frame drift); their read-backs are the proof; the two captures check frame reachability.
# Expected counts: 1 CreateDevice, 2 queued reflection resets = 4 Resets all 0, 8 successful row changes;
# cube 256/1 level then 1024/11, contact 0/1, smoke 0/1, mode Native then Borderless.
# Broken control: `runtime/live_picture_check.sh <dir> NFS_TEST_LIVE_PICTURE_BROKEN=1` (the request does nothing; diag runs
# only) must print FAIL: 0 queued, 0 Resets and no cube re-creation.
# Offline synthetic logs (live_picture_check_test.py): PASS 0 findings; missing resets FAIL 2 findings.
set -u
cd "$(dirname "$0")/.."
. runtime/env.sh
nfs_need_py
case "${1:-}" in
    --sweep|--list|--plan|--sweep-logs)
        mode=${1#--}; shift
        exec "$TOOLS_PY" -I runtime/live_picture_sweep.py "$mode" "$@" ;;
esac
launch=1
if [ "${1:-}" = --logs ]; then launch=0; shift; fi
out=${1:?scratch-dir}; shift
keys='seq:sim:14,ESCAPE,wait:60,shot,wait:20,set:graphics.look_contact_shadows=off,wait:30,set:graphics.look_contact_shadows=on,wait:30,set:graphics.look_soft_smoke=off,wait:30,set:graphics.look_soft_smoke=on,wait:30,set:graphics.look_reflections=pc,wait:40,set:graphics.look_reflections=sharp,wait:40,set:display.fullscreen=native,wait:30,set:display.fullscreen=borderless,wait:30,shot'
if [ "$launch" = 1 ]; then
    rm -rf "$out" "$out.user"
    runtime/run_guarded.sh 150 "$out" BOOT_RACE=15.1.1 BOOT_AUTODRIVE=1 NFS_LOCKSTEP=1 NFS_HDR_OUTPUT= "BOOT_KEYS=$keys" "$@" > "$out.launch" 2>&1
fi
"$TOOLS_PY" -I - "$out" <<'PY'
import glob, re, sys
out = sys.argv[1]
ev = open(f'{out}/events.log', errors='replace').read().splitlines()
con = open(f'{out}.out', errors='replace').read().splitlines() if glob.glob(f'{out}.out') else []
fails = []
def need(ok, msg):
    if not ok: fails.append(msg)
creates = [l for l in ev if 'IDirect3D9::CreateDevice' in l]
need(len(creates) == 1, f'{len(creates)} D3D9 CreateDevice (want 1)')
need(not any('restart' in l.lower() and 'boot:' in l for l in con), 'restart marker in the console log')
resets = [l for l in ev if 'IDirect3DDevice9::Reset' in l]
bad = [l for l in resets if not re.search(r'-> 0\b', l)]
queued = sum('live picture: reset requested' in l for l in ev)
need(queued >= 2, f'{queued} queued resets (want >= 2)')
need(len(resets) >= 2 * 2 and not bad, f'{len(resets)} Resets, {len(bad)} not 0 (want >= 4, all 0)')
need(len(resets) == 2 * queued, f'{len(resets)} Resets for {queued} queued (want 2 each)')
need(not glob.glob(f'{out}/hang_*') and not glob.glob(f'{out}/stall_*'), 'hang_N/stall_N file')
need(sum(' -> 1' in l and 'script: set:' in l for l in ev) == 8, 'a set: step did not change its row')
def after(tag, pat):                       # the first line matching pat after the script line `tag`
    i = next((k for k, l in enumerate(ev) if f'script: set:{tag}' in l), None)
    if i is None: return None
    return next((l for l in ev[i:] if re.search(pat, l)), None)
def before(tag, pat):                      # the last line matching pat before the script line `tag` (a reset happens before it is logged)
    i = next((k for k, l in enumerate(ev) if f'script: set:{tag}' in l), None)
    if i is None: return None
    return next((l for l in reversed(ev[:i]) if re.search(pat, l)), None)
cubes = {}
for v, edge in (('pc', 256), ('sharp', 1024)):
    l = after(f'graphics.look_reflections={v}', r'envcube: create .*usage=1 ')   # the Reset runs at the next frame start
    m = re.search(r'edge=(\d+) levels=(\d+)', l or '')
    cubes[v] = (int(m[1]), int(m[2])) if m else None
    need(cubes[v] and cubes[v][0] == edge and (v == 'pc' or cubes[v][1] == 11), f'reflections {v}: cube {cubes[v]} (want edge {edge}' + (', 11 levels)' if v == 'sharp' else ')'))
state = {}
for tag in ('graphics.look_contact_shadows=off', 'graphics.look_contact_shadows=on', 'graphics.look_soft_smoke=off', 'graphics.look_soft_smoke=on'):
    l = before(tag, r'live picture: contact shadows=')
    m = re.search(r'shadows=(\d) soft smoke switch=(\w+)', l or '')
    state[tag] = (int(m[1]), m[2]) if m else None
need(state['graphics.look_contact_shadows=off'] and state['graphics.look_contact_shadows=off'][0] == 0 and state['graphics.look_contact_shadows=on'][0] == 1, f'contact shadows read-back {state}')
need(state['graphics.look_soft_smoke=off'] and state['graphics.look_soft_smoke=off'][1] == 'off' and state['graphics.look_soft_smoke=on'][1] == 'unset', f'soft smoke read-back {state}')
modes = [re.search(r'mode=(\w+)', l)[1] for l in con if 'fullscreen: mode=' in l]
need(modes[-2:] == ['Native', 'Borderless'], f'fullscreen mode lines {modes} (want ... Native, Borderless)')
shots = sorted(glob.glob(f'{out}/frame_*.png'))
need(len(shots) >= 2, f'{len(shots)} frames (want >= 2)')
print(f'CreateDevice {len(creates)}, queued {queued}, Resets {len(resets)} (bad {len(bad)}); '
      f'cubes {cubes}; contact/smoke read-backs {list(state.values())}; modes {modes}')
print('live_picture_check: ' + ('PASS' if not fails else 'FAIL: ' + '; '.join(fails)))
sys.exit(1 if fails else 0)
PY
