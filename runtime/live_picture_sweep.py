#!/usr/bin/env python3
"""Derived live-row sweep; used by live_picture_check.sh --sweep/--list/--plan/--sweep-logs.

Offline proof: identical frames PASS 0.00%; omitted brightness undo FAIL by row
at 100.00%; missing row, step, capture and changed dimensions also fail closed.
Game measurements remain in the scratch directory, never in the source tree.

**Live undo sweep:** `runtime/live_picture_check.sh --sweep <new-dir>` derives
every callback-bearing row without a restart flag from the private build's C
settings tables, prints the count and IDs, and checks that inventory against the
actual game registry on every run. `--list` and `--plan` are offline. The current
inventory is 63 value rows, including the hidden preset-owned rows, and 18 command,
text or information rows. Commands such as Quit, Join and Save report have no
non-default value to toggle; they are listed separately and never invoked.
Derived getters such as Look preset summarize other rows and may not round-trip;
their explicit Setting.derived marker also classifies them as commands.
New declarations/macros are discovered, and unrepresentable live IDs or runtime
inventory drift fail closed. Public builds cannot execute the diagnostic driver.

The sweep uses fresh guarded race 15.1.1 profiles, no rivals, autodrive, lockstep
60 Hz and a fixed 1280x720 render size. At sim 10 the diagnostic driver normalizes
live value rows to their declared defaults in both the baseline and test runs
(including getter-backed window/Video settings). The baseline receives no on/undo
changes. Each batch of up to eight rows changes to non-default values at the
sim-13 script barrier and returns to defaults at the sim-16 barrier; each phase
runs all batch callbacks in one script step. Choices use the next index and
sliders use the other endpoint. Script steps run on successive ticks, so callback
dispatch is one tick after its sim barrier. At sim 18, compare the full chase
capture to the baseline with `refs_compare.py`'s chase tolerance: 8-pixel blocks,
12 levels/channel, at most 3% changed blocks. Check row changes/readbacks,
CreateDevice count, successful Resets, restart and hang/stall markers too.
Inherited row switches are cleared; null audio remains pinned by the guard.
Both pad slots are simulated and absent throughout the run, so exercising the
rumble callback cannot reach physical motors. The diagnostics-only driver temporarily bypasses
row environment locks to exercise those callbacks and restores each lock.

Nine batches plus baseline have initial caps of 750 seconds. Rows driving the
same window transition run in separate batches so each settles before readback.
Failed batches split into fresh runs until singleton row failures identify the ID and label.
Failed parent batches remain failures even when their halves pass (an interaction
is still a regression). The entire capture/isolation phase has a 1500-second
wall deadline; exhausted rows print FAIL by name as unverified, never PASS.
Missing/corrupt images, changed dimensions, lost script steps and inventory drift
also fail. A new scratch directory is required; results, inventory and run order
are saved there. `--sweep-logs <dir>` rechecks captures without launching.

Broken control: `runtime/live_picture_check.sh --sweep <another-new-dir>
--skip-undo graphics.brightness` sets `NFS_TEST_LIVE_SWEEP_SKIP_UNDO` to omit that
row's undo and must exit 1 with `FAIL graphics.brightness`. The normal driver
uses `NFS_TEST_LIVE_SWEEP_ROWS` for the batch IDs. Offline
`live_picture_sweep_test.py` (discovered by `offline_tests.sh`) compiles the real
driver with a synthetic registry: normal 0.00% PASS, skipped brightness undo
100.00% FAIL by name. It also checks source discovery, batch coverage, budget
exhaustion and fail-closed comparisons. Live captures and this failure seed still
require a game run; these offline numbers are synthetic.

"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

RT = Path(__file__).resolve().parent
ROOT = RT.parent
sys.path.insert(0, str(RT))
import refs_compare as refs

KEYS = 'seq:sim:10,sweep:init,sim:13,sweep:on,sim:16,sweep:undo'
BATCH_SIZE = 8
# Both callbacks change the Cocoa fullscreen mode; a native transition blocks a window toggle.
COUPLED_ROWS = (frozenset({'display.window', 'display.fullscreen'}),)
RUN_SECONDS = 75
TOTAL_SECONDS = 25 * 60
VALUED = {'SET_TOGGLE', 'SET_CHOICE', 'SET_SLIDER'}


def valued(row):
    return row['type'] in VALUED and not row.get('derived', False)


TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|\d+|[^\s]', re.S)


def fields(tokens):
    """Split a C initializer at outer commas, preserving compound literals."""
    parts, part, depth = [], [], 0
    for token in tokens + [',']:
        if token == ',' and depth == 0:
            if part:
                if len(part) < 4 or part[0] != '.' or part[2] != '=':
                    raise ValueError(f'unsupported Setting initializer: {part[:8]}')
                parts.append((part[1], part[3:]))
            part = []
        else:
            part.append(token)
            depth += (token in ('{', '(', '[')) - (token in ('}', ')', ']'))
    return dict(parts)


def build_sources(root):
    """Private build inputs, including the selected source-variable branch."""
    build = (root / 'runtime/build.sh').read_text()
    selected = build.split('if [ "${NFS_PUBLIC:-0}" = 1 ]; then', 1)[1]
    private = selected.split('\nelse\n', 1)[1].split('\nfi\n', 1)[0]
    command = build.split('\nclang \\\n', 1)[1]
    return sorted(set(re.findall(r'^\s+([\w-]+\.(?:c|m|mm))\s*\\?\s*$', private + '\n' + command, re.M)))


def derive_rows(root=ROOT):
    """Read production initializers, not a hand-maintained expected row list.

    Preprocess without includes: only the table's own conditional declarations
    and macros matter here. Numeric domains/defaults are read from the real
    registry by the game driver, so no C expression evaluator is duplicated.
    Runtime registry equality guards dynamic/new declarations and build drift.
    """
    rows = []
    for name in build_sources(root):
        path = root / 'runtime' / name
        source = path.read_text()
        if not re.search(r'\bSetting\b', source) or '.apply' not in source:
            continue
        source = re.sub(r'^\s*#\s*include[^\n]*', '', source, flags=re.M)
        cpp = subprocess.run(['clang', '-E', '-P', '-x', 'c', '-'], input=source,
                             capture_output=True, text=True, check=True)
        tokens = [m[0] for m in TOKEN.finditer(cpp.stdout) if not m[0].startswith(('//', '/*'))]
        for i in range(len(tokens) - 4):
            if tokens[i:i+4] != ['{', '.', 'id', '=']:
                continue
            depth, end = 1, i + 1
            while depth:
                depth += (tokens[end] == '{') - (tokens[end] == '}')
                end += 1
            f = fields(tokens[i+1:end-1])
            if 'apply' not in f or f['apply'] in (['0'], ['NULL']):
                continue
            if f.get('restart', ['0']) == ['1']:
                continue
            if f.get('restart', ['0']) != ['0']:
                raise ValueError(f'{name}: nonliteral restart flag requires review')
            if len(f['id']) != 1 or not f['id'][0].startswith('"'):
                raise ValueError(f'{name}: dynamic live id requires review')
            if f.get('derived', ['0']) not in (['0'], ['1']):
                raise ValueError(f'{name}: nonliteral derived flag requires review')
            rid = json.loads(f['id'][0])
            label = f.get('label', f['id'])
            rows.append(dict(id=rid, label=json.loads(label[0]) if label[0].startswith('"') else rid,
                             type=''.join(f.get('type', ['SET_TOGGLE'])), source=name,
                             env=json.loads(f['env'][0]) if 'env' in f else None,
                             hidden=f.get('hidden', ['0']) != ['0'],
                             derived=f.get('derived', ['0']) == ['1']))
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('empty or duplicate live-row inventory')
    return sorted(rows, key=lambda r: r['id'])


def batches(rows, size=BATCH_SIZE):
    if size < 1:
        raise ValueError('batch size must be positive')
    groups, group, ids = [], [], set()
    for row in rows:
        rid = row['id']
        coupled = any(rid in pair and ids & pair for pair in COUPLED_ROWS)
        if group and (len(group) == size or coupled):
            groups.append(group)
            group, ids = [], set()
        group.append(row)
        ids.add(rid)
    if group:
        groups.append(group)
    return groups


def picture(base, shot):
    try:
        result = refs.measure(refs.load(base), refs.load(shot), 'chase')
        if result is None:
            return False, 'capture dimensions differ'
        return result[0] <= refs.MAX_BLOCKS['chase'], f'{result[0]:.2f}% blocks (limit {refs.MAX_BLOCKS["chase"]:.1f}%)'
    except (OSError, ValueError) as exc:
        return False, f'capture missing/invalid: {exc}'


def audit(folder, inventory, ids):
    try:
        events = (folder / 'events.log').read_text(errors='replace')
        console = Path(str(folder) + '.out').read_text(errors='replace')
    except OSError as exc:
        return [str(exc)]
    errors = []
    expected = {r['id'] for r in inventory if valued(r)}
    observed = re.findall(r'live sweep: row (\S+)', events)
    commands = re.findall(r'live sweep: command (\S+)', events)
    if len(observed) != len(expected) or set(observed) != expected:
        errors.append(f'registry mismatch: missing={sorted(expected-set(observed))} extra={sorted(set(observed)-expected)}')
    if set(commands) != {r['id'] for r in inventory if not valued(r)}:
        errors.append('command inventory mismatch')
    if events.count('IDirect3D9::CreateDevice') != 1:
        errors.append('expected exactly one CreateDevice')
    if re.search(r'^boot: restart\b', console, re.I | re.M) or 'live sweep: ERROR' in events:
        errors.append('restart or driver error')
    if list(folder.glob('hang_*')) or list(folder.glob('stall_*')):
        errors.append('hang/stall marker')
    resets = [line for line in events.splitlines() if 'IDirect3DDevice9::Reset' in line]
    if any(not re.search(r'-> 0\b', line) for line in resets):
        errors.append('failed device Reset')
    records = re.findall(r'live sweep: (init|on|undo) (\S+) result=(-?\d+) read=(-?\d+) want=(-?\d+) default=(-?\d+)', events)
    for phase, names in (('init', expected), ('on', ids), ('undo', ids)):
        selected = [r for r in records if r[0] == phase]
        if len(selected) != len(names) or {r[1] for r in selected} != set(names):
            errors.append(f'{phase}: missing/duplicate/unexpected row steps')
        for _, rid, result, read, want, default in selected:
            if read != want or (phase != 'init' and result != '1') or (phase == 'on' and want == default):
                errors.append(f'{rid}: {phase} result={result} read={read} want={want} default={default}')
    return errors


class Runner:
    def __init__(self, out, inventory, skip_undo=None):
        self.out, self.inventory, self.skip_undo = out, inventory, skip_undo
        self.deadline = time.monotonic() + TOTAL_SECONDS
        self.number = 0
        self.records = []

    def run(self, ids):
        remaining = self.deadline - time.monotonic()
        if remaining < 10:
            raise TimeoutError('25-minute sweep budget exhausted')
        name = f'run-{self.number:03d}'
        self.number += 1
        folder = self.out / name
        seconds = max(1, min(RUN_SECONDS, int(remaining) - 9))
        # Clear inherited row locks; keep null sound and disabled physical feedback.
        env = {k: v for k, v in os.environ.items() if not k.startswith(('BOOT_', 'NFS_'))}
        env.update({k: os.environ[k] for k in ('BOOT_BIN', 'NFS_GAME', 'NFS_METAL_CACHE', 'NFS_SLOT') if k in os.environ})
        args = [str(RT / 'run_guarded.sh'), str(seconds), str(folder),
                'BOOT_RACE=15.1.1', 'BOOT_RACE_AI=0', 'BOOT_AUTODRIVE=1',
                'BOOT_PAD=sim:xbox@999999', 'BOOT_PAD2=sim:xbox@999999',
                'NFS_SIMHZ=60', 'NFS_LOCKSTEP=1', 'NFS_DISPLAY_PIXELS=2560x1440',
                'BOOT_REG=g_RacingResolution=0,g_FSAALevel=2,g_ShadowDetail=2,g_RoadReflectionEnable=2,g_RainEnable=1',
                'BOOT_DIAG_SIMSHOTS=18', f'BOOT_KEYS={KEYS}',
                f'NFS_TEST_LIVE_SWEEP_ROWS={",".join(ids)}',
                f'NFS_TEST_LIVE_SWEEP_SKIP_UNDO={self.skip_undo or ""}']
        print(f'run {name}: {",".join(ids) or "baseline"}', flush=True)
        record = dict(folder=name, ids=ids)
        self.records.append(record)
        (self.out / 'runs.json').write_text(json.dumps(self.records, indent=2) + '\n')
        with (self.out / f'{name}.launch').open('w') as log:
            process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            end = min(self.deadline - 8, time.monotonic() + seconds)
            try:
                while process.poll() is None and time.monotonic() < end:
                    shot = folder / 'simshot_018.00.png'
                    # Let the asynchronous encoder close the PNG before ending the owned supervisor.
                    if shot.exists():
                        try:
                            refs.load(shot)
                            break
                        except (OSError, ValueError):
                            pass
                    time.sleep(0.2)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        return folder


def sweep(rows, capture, report):
    """Fresh runs for each batch; recursive isolation never accepts a failed parent."""
    base = capture([])
    base_errors = audit(base, rows, [])
    ok, metric = picture(base / 'simshot_018.00.png', base / 'simshot_018.00.png')
    if base_errors or not ok:
        report('FAIL baseline: ' + '; '.join(base_errors + ([] if ok else [metric])))
        return False
    passed = True

    def check(group):
        nonlocal passed
        ids = [r['id'] for r in group]
        try:
            folder = capture(ids)
        except TimeoutError as exc:
            passed = False
            for row in group:
                report(f'FAIL {row["id"]} ({row["label"]}): {exc}; unverified')
            return
        errors = audit(folder, rows, ids)
        same, metric = picture(base / 'simshot_018.00.png', folder / 'simshot_018.00.png')
        if not errors and same:
            for row in group:
                report(f'PASS {row["id"]} ({row["label"]}): {metric}')
        elif len(group) > 1:
            report(f'FAIL batch {",".join(ids)}: {"; ".join(errors) or metric}; isolating')
            # Even if both halves pass, the original interaction remains a failure.
            passed = False
            middle = len(group) // 2
            check(group[:middle])
            check(group[middle:])
        else:
            passed = False
            report(f'FAIL {ids[0]} ({group[0]["label"]}): {"; ".join(errors) or metric}')

    for group in batches([r for r in rows if valued(r)]):
        check(group)
    return passed


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['sweep', 'list', 'plan', 'sweep-logs'])
    parser.add_argument('out', nargs='?', type=Path)
    parser.add_argument('--skip-undo', help='deliberately omit this row\'s undo; must FAIL by name')
    args = parser.parse_args()
    rows = derive_rows()
    values = [r for r in rows if valued(r)]
    print(f'live sweep: {len(values)} value rows with .apply and no .restart; {len(rows)-len(values)} command rows (no value to toggle)')
    for row in rows:
        print(f'{"ROW" if valued(row) else "COMMAND"} {row["id"]} ({row["label"]}) [{row["source"]}{", hidden" if row["hidden"] else ""}]')
    if args.mode == 'list':
        return 0
    if args.skip_undo and args.skip_undo not in {r['id'] for r in values}:
        parser.error('--skip-undo must name a derived value row')
    planned = 1 + len(batches(values))
    print(f'plan: {planned} initial guarded runs, batches <= {BATCH_SIZE}; {planned*RUN_SECONDS}s initial caps; hard total {TOTAL_SECONDS}s including failure isolation')
    if args.mode == 'plan':
        return 0
    if args.out is None:
        parser.error('scratch directory required')
    out = args.out.resolve()
    if args.mode == 'sweep':
        out.mkdir(parents=True, exist_ok=False)
        (out / 'inventory.json').write_text(json.dumps(rows, indent=2) + '\n')
        runner = Runner(out, rows, args.skip_undo)
        runner.deadline = started + TOTAL_SECONDS
        with (out / 'results.txt').open('w') as log:
            def report(line):
                print(line, flush=True)
                print(line, file=log, flush=True)
            good = sweep(rows, runner.run, report)
    else:
        recorded = json.loads((out / 'inventory.json').read_text())
        if recorded != rows:
            raise ValueError('recorded inventory differs from current settings table')
        runs = json.loads((out / 'runs.json').read_text())
        iterator = iter(runs)
        def replay(ids):
            run = next(iterator)
            if run['ids'] != ids:
                raise ValueError('recorded batch ordering differs')
            return out / run['folder']
        good = sweep(rows, replay, print)
        if next(iterator, None) is not None:
            raise ValueError('unexpected extra recorded runs')
    print('live_picture_sweep: ' + ('PASS' if good else 'FAIL'))
    return 0 if good else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError, StopIteration) as exc:
        print(f'live_picture_sweep: FAIL: {exc}', file=sys.stderr)
        sys.exit(1)
