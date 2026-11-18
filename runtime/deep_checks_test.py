#!/usr/bin/env python3
"""Synthetic regressions for four gate checks, with pinned pre-fix shell fragments.

Old -> current exit status: silent roam with no usable RMS (0 -> 1), zero
differential comparisons or selector error (0 -> 1), both UI frames plus
a stall report (0 -> 1), and zero fake-audio checks or a failed child (0 -> 1).
Only marked shell blocks and fixture children run. No game or full gate launch.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FAIL = 'fail() { echo "GATE FAIL: $*"; exit 1; }\n'
LEGACY = {
    'mix': ('3e80458ead9765748ac654f8294727afecc0716627efc6acf0eaf5bc2e49cf98', r'''refused=$(grep -c 'x360audio: bank load .*result -4' "$d/events.log" 2>/dev/null)
overflow=$(grep -c 'SLOT OVERFLOW' "$d/events.log" 2>/dev/null)
[ "${overflow:-0}" = 0 ] || { echo "MIXSMOKE FAIL: $overflow bank slot overflows ($(grep -m1 'SLOT OVERFLOW' "$d/events.log" | cut -c1-160); $d)"; exit 1; }
ingame=$(grep -c ' flow 6 ' "$d/stats.log" 2>/dev/null)
[ "${ingame:-0}" -ge 20 ] || { echo "MIXSMOKE FAIL: reached free roam for ${ingame:-0} s (need 20; $d)"; exit 1; }
med=$(grep ' flow 6 ' "$d/stats.log" | sed -n '3,20p' | sed -E 's/.*mixrms ([0-9.]+) ([0-9.]+) .*/\1 \2/' |
      awk '{print $1 + $2}' | sort -n | awk '{v[NR]=$1} END {printf "%.3f", NR ? v[int((NR+1)/2)] : 0}')
first=$(grep -m1 'x360audio: bank load .*result -4' "$d/events.log" 2>/dev/null | sed -E 's/.*bank load ([^:]*):.*/\1/')
if [ "${refused:-0}" != 0 ] || [ "$(echo "$med < 0.01" | bc)" = 1 ]; then
    echo "MIXSMOKE FAIL: $refused refused bank loads${first:+ (first $first)}, in-game FL+FR median $med (need 0 and >= 0.01; $d)"
    exit 1
fi
echo "MIXSMOKE PASS: FE -> free roam with the X360 mix: 0 refused bank loads, in-game FL+FR median $med"
'''),
    'diff': ('1dd1ca922f524345a90ac6997a936b32bc7467602a578efeab9fb0a84da652d5', r'''if "$py" -I "$here/recomp/build_inputs.py" --changed "$base"; then
    (cd "$NFS_PARENT" && "$py" -I "$here/recomp/difftest.py" --synth) > "$log/difftest.log" 2>&1
    grep -q " 0 fail" "$log/difftest.log" || fail "difftest: $(grep -m1 'differential' "$log/difftest.log")"
    note="$note difftest $(grep -o '[0-9]* pass' "$log/difftest.log")"
fi
'''),
    'ui': ('d9afd4ecdbaa750300c1f1e7f802b28a8161d96f7b5f82f6cfdb59b8d686f36a', r'''capture() {
    out=$1; mkdir -p "$out"
    race_launch
    run ui frame_01800.png 60 BOOT_REG=g_RacingResolution=0 BOOT_DIAG_SHOTS=900,1800 BOOT_DIAG_FIXED_STEP=1 \
        BOOT_KEYS=seq:wait:960,RETURN,wait:240,RETURN,wait:240
    cp "$out/race/simshot_012.00.png" "$out/chase.png" 2>/dev/null
    cp "$out/race/simshot_025.00.png" "$out/hood.png" 2>/dev/null
    cp "$out/race/simshot_011.50.png" "$out/rain.png" 2>/dev/null
    cp "$out/ui/frame_00900.png" "$out/title.png" 2>/dev/null
    cp "$out/ui/frame_01800.png" "$out/menu.png" 2>/dev/null
    for f in chase hood rain title menu; do
        [ -s "$out/$f.png" ] || { echo "REFS FAIL: $f not captured (see $out/race.out, $out/ui.out)"; return 1; }
    done
    [ -n "${REFS_KEEP:-}" ] || rm -rf "$out/race" "$out/race.user"
    rm -rf "$out/ui" "$out/ui.user"
}

'''),
    'audio': ('1306fe52fd159f75fcb742f6c2c69d357be92a5b5cb0cdf5692fbbb3d6344e92', r'''wait "$dstest_pid"; dstest_pid=
grep -q " 0 failed" "$log/dstest.txt" || fail "audio: dstest fake: $(grep -m1 -E 'FAIL|failed' "$log/dstest.txt")"
'''),
}


def legacy(name):
    digest, fragment = LEGACY[name]
    assert hashlib.sha256(fragment.encode()).hexdigest() == digest
    return fragment


def block(path, name):
    source = (ROOT / path).read_text()
    return source.split(f'# BEGIN offline {name}', 1)[1].split('\n', 1)[1].split(f'# END offline {name}', 1)[0]


def shell(code, cwd, *args, status=0, output='', **env):
    result = subprocess.run(
        ['/bin/sh', '-c', code, 'deep-checks', *map(str, args)],
        cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == status and output in result.stdout, (
        status, output, result.returncode, result.stdout, result.stderr
    )
    return result.stdout


def mixsmoke(tmp):
    evidence = 'd="$PWD"\n' + block('runtime/mixsmoke.sh', 'audio evidence')
    old = 'd="$PWD"\n' + legacy('mix')
    events = tmp / 'events.log'
    stats = tmp / 'stats.log'
    events.write_text('')
    row = 't 40.0 frame 1200 mixrms {} flow 6 end\n'
    # diag.c emits the (n/a) flag before the numeric fields when RMS is unavailable.
    stats.write_text(row.format('(n/a) 0.000 0.000 0.000 0.000 0.000 0.000') * 20)
    shell(old, tmp, output='MIXSMOKE PASS:')
    shell(evidence, tmp, status=1, output='0 valid FL/FR RMS samples')
    valid = row.format('0.150 0.100 0.000 0.000 0.000 0.000')
    stats.write_text(valid * 19 + row.format('0..2 0.100 0.000 0.000 0.000 0.000') + valid)
    shell(evidence, tmp, status=1, output='17 valid FL/FR RMS samples')
    stats.write_text(valid * 20)
    shell(evidence, tmp, output='FL+FR median 0.250')
    events.unlink()
    shell(old, tmp, output='MIXSMOKE PASS:')
    shell(evidence, tmp, status=1, output='missing or unreadable')
    events.mkdir()
    shell(evidence, tmp, status=1, output='missing or unreadable')
    events.rmdir()
    events.write_text('x360audio: bank load test: result -4\n')
    shell(evidence, tmp, status=1, output='1 refused bank loads')
    events.write_text('SLOT OVERFLOW\n')
    shell(evidence, tmp, status=1, output='1 bank slot overflows')
    events.write_text('')
    stats.write_text(row.format('0.000 0.000 0.000 0.000 0.000 0.000') * 20)
    shell(evidence, tmp, status=1, output='FL+FR median 0.000')
    print('MixSmoke: silent roam 0/18 valid RMS, old PASS, current FAIL. Valid roam 18/18, median 0.250.')


def differential(tmp):
    selector = ROOT / 'recomp/build_inputs.py'
    git = tmp / 'git'
    git.write_text('''#!/bin/sh
case "$1:$2" in
    diff:--name-only) [ "$3" != bad-base ] || exit 128; printf '%s' "${DIFF_PATH:-}" ;;
    ls-files:--others) : ;;
    *) exit 99 ;;
esac
''')
    git.chmod(0o755)
    prefix = FAIL + '''
py=fixture_python; here="$PWD"; NFS_PARENT="$PWD"; log="$PWD"; note=; base="$1"
fixture_python() {
    case "$2" in
        */build_inputs.py) "$FIXTURE_PY" -I "$SELECTOR" --changed "$4" ;;
        */difftest.py) printf '%s\\n' "$DIFF_SUMMARY"; return "$DIFF_STATUS" ;;
    esac
}
'''
    current = prefix + block('runtime/gate.sh', 'differential stage') + '\nprintf "%s\\n" "$note"'
    old = prefix + legacy('diff')
    good = ('differential test: 375 functions sampled, 374 pass, 0 fail (0 crash/timeout), '
            '1 expected-fail, 0 no-verdict, states skipped because the original faulted: 2, 375 synthetic cases')
    empty = ('differential test: 370 functions sampled, 0 pass, 0 fail (0 crash/timeout), '
             '0 expected-fail, 370 no-verdict, states skipped because the original faulted: 2960, 370 synthetic cases')

    def stage(code, summary=good, *, base='base', path='recomp/runtime.h', child=0, **expect):
        return shell(code, tmp, base, FIXTURE_PY=sys.executable, SELECTOR=str(selector),
                     DIFF_SUMMARY=summary, DIFF_STATUS=str(child), DIFF_PATH=path,
                     PATH=f'{tmp}:{os.environ["PATH"]}', **expect)

    stage(old, empty)
    stage(current, empty, status=1, output='incomplete or failing synthetic verdicts')
    stage(old, base='bad-base')
    stage(current, base='bad-base', status=1, output='selector exit 1')
    stage(current, path='recomp/difftest_synth.log', output='difftest unchanged;')
    stage(current, output='difftest 374 pass;')
    stage(current, good.replace('375 functions sampled', '374 functions sampled'), status=1)
    stage(current, good.replace('374 pass', '373 pass'), status=1)
    stage(current, good + '\n' + good, status=1)
    stage(current, good, child=7, status=1, output='difftest: exit 7')
    print('Differential: 0/370 verdicts and selector exit 1, old PASS, current FAIL. 374 PASS + 1 XFAIL accepted.')


def ui_refs(tmp):
    runtime = tmp / 'runtime'
    runtime.mkdir()
    runner = runtime / 'run_guarded.sh'
    runner.write_text('''#!/bin/sh
mkdir -p "$2" "$2.user"
printf 'frame\\n' > "$2/frame_00900.png"
printf 'frame\\n' > "$2/frame_01800.png"
touch "$2.out"
[ -z "$UI_REPORT" ] || touch "$2/$UI_REPORT"
[ -z "$UI_STATUS" ] || printf 'run_guarded: exit %s (fixture)\\n' "$UI_STATUS"
exit "$LAUNCHER_STATUS"
''')
    runner.chmod(0o755)
    race = '''
race_launch() {
    mkdir -p "$out/race"
    for t in 011.50 012.00 025.00; do echo frame > "$out/race/simshot_$t.png"; done
}
capture "$PWD/refs"
'''
    current = block('runtime/refs.sh', 'capture') + '\nsleep() { :; }\n' + race
    old = legacy('ui') + '\nrun() { runtime/run_guarded.sh "$3" "$out/$1" > "$out/ui.guard.out"; }\n' + race

    def capture(code, *, report='', ui='0', launcher='0', **expect):
        return shell(code, tmp, UI_STATUS=ui, UI_REPORT=report,
                     LAUNCHER_STATUS=launcher, NFS_RUNS=str(tmp / 'state'), **expect)

    capture(old, report='stall_1.txt')
    ui = tmp / 'refs' / 'ui'
    assert not ui.exists()
    for report in ('stall_1.txt', 'hang_1.txt', 'crash.txt'):
        capture(current, report=report, status=1, output=f'REFS FAIL: UI report {report}')
        assert (ui / report).exists() and (ui / 'frame_01800.png').exists()
        (ui / report).unlink()
    capture(current, ui='139', status=1, output='game exit 139')
    capture(current, ui='143', status=1, output='game exit 143')
    capture(current, ui='', status=1, output='game exit record missing')
    capture(current, launcher='7', status=1, output='launcher exit 7')
    # Exercise the planned stop with the final marker present and the game
    # identified by the slot and confirmed by the runner's child list.
    (tmp / 'state' / 'slots').mkdir(parents=True)
    (tmp / 'state' / 'slots' / '42').write_text('run 43 shared\n')
    guard = tmp / 'refs' / 'ui.guard.out'
    guard.write_text('run_guarded: exit 143 (stopped by the guard)\n')
    shell(block('runtime/refs.sh', 'capture') + '''
out="$PWD/refs"; NFS_RUNS="$PWD/state"; last=frame_01800.png; rg=42
guard_log="$out/ui.guard.out"
pgrep() { [ "$*" = "-P 42" ] && echo 43; }
kill() { [ "$*" = "-TERM 43" ]; }
wait() { [ "$*" = 42 ]; }
finish_ui
''', tmp)
    capture(current)
    assert not ui.exists() and not guard.exists()
    assert (tmp / 'refs' / 'menu.png').read_text() == 'frame\n'
    print('UI references: 2 frames plus 1 stall report, old PASS with cleanup, current FAIL with diagnostics retained.')


def fake_audio(tmp):
    launch = FAIL + '''
log="$PWD"
(printf '%s\\n' "$1"; exit "$2") > "$log/dstest.txt" &
dstest_pid=$!
'''
    current = launch + block('runtime/gate.sh', 'result checks') + '\ncheck_dstest_result; echo "$nd"'
    old = launch + legacy('audio')
    zero = 'dstest fake: 0 checks, 0 failed'
    good = 'dstest fake: 39 checks, 0 failed'
    shell(old, tmp, zero, 0)
    shell(current, tmp, zero, 0, status=1, output='positive checks')
    shell(old, tmp, good, 7)
    shell(current, tmp, good, 7, status=1, output='child exit 7')
    shell(current, tmp, f'noise {good}', 0, status=1)
    shell(current, tmp, f'{good} extra', 0, status=1)
    shell(current, tmp, good.replace('0 failed', '1 failed'), 0, status=1)
    shell(current, tmp, good, 0, output='39 checks')
    print('Fake audio: 0 checks or child exit 7, old PASS, current FAIL. Anchored 39 checks, child exit 0 accepted.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='deep-checks-') as tmp:
        for check in (mixsmoke, differential, ui_refs, fake_audio):
            directory = Path(tmp) / check.__name__
            directory.mkdir()
            check(directory)
