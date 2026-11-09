#!/usr/bin/env python3
"""Offline sweep proof using the actual diagnostic driver, table discovery and refs comparator.

Normal: 2 rows PASS, 0.00% changed blocks. Skip brightness undo: FAIL
graphics.brightness by name, 100.00% changed blocks. Missing step/row/image,
bad readback/reset/restart and dimension changes fail; 3% noise passes, 4% fails.
No game, window, device or sockets are opened.
Derived choice: 1 command, 0 value steps; the unmarked classifier reports a row.
Coupled rows: 0 shared batches; original planner FAILS with 1 shared batch.
Uncoupled interaction: 1 failed batch, 2 passing singletons still ends FAIL;
removing the failed-parent latch falsely ends PASS and fails this check.
"""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from unittest import mock

RT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sweep', RT / 'live_picture_sweep.py')
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)
import numpy as np
from PIL import Image


HARNESS = r'''
#include "settings.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int diag_on = 1;
static int brightness = 100;
static void bright(int v) { brightness = v; }
static int get_bright(void) { return brightness; }
static void other(int v) { (void)v; }
static int summary(void) { return 3; }
static int preset_calls;
static void preset(int v) { (void)v; ++preset_calls; }
static Setting rows[] = {
    {.id="graphics.brightness", .type=SET_SLIDER, .min=0, .max=100, .step=1,
     .def=100, .value=100, .apply=bright, .get=get_bright, .locked=1},
    {.id="test.toggle", .type=SET_TOGGLE, .def=1, .value=1, .apply=other},
    {.id="test.command", .type=SET_ACTION, .apply=other},
    {.id="test.restart", .type=SET_TOGGLE, .restart=1, .apply=other},
    {.id="test.derived", .type=SET_CHOICE, .nchoices=4, .def=0,
     .pref="", .own_pref=1, .get=summary, .apply=preset, .derived=1}
};
int settings_count(void) { return sizeof(rows)/sizeof(rows[0]); }
Setting *settings_at(int i) { return rows+i; }
Setting *settings_find(const char *id) {
    for(int i=0;i<settings_count();i++) if(!strcmp(id,rows[i].id)) return rows+i;
    return NULL;
}
int settings_set(Setting *s,int v) {
    if(s->locked || (s->get ? s->get() : s->value)==v) return 0;
    s->value=v; s->apply(v); return 1;
}
void diag_event(const char *fmt,...) {
    va_list ap; va_start(ap,fmt); vprintf(fmt,ap); va_end(ap); putchar('\n');
}
void live_picture_sweep_step(const char *phase);
int main(int argc,char **argv) {
    (void)argc;
    diag_on=atoi(argv[1]);
    live_picture_sweep_step("init");
    live_picture_sweep_step("on");
    live_picture_sweep_step("undo");
    fprintf(stderr,"pixel=%d locked=%d preset_calls=%d\n",brightness,rows[0].locked,preset_calls);
    return 0;
}
'''


def inventory():
    return [dict(id='graphics.brightness', label='Brightness', type='SET_SLIDER', source='driver.c', hidden=False),
            dict(id='test.toggle', label='Toggle', type='SET_TOGGLE', source='driver.c', hidden=False),
            dict(id='test.command', label='Command', type='SET_ACTION', source='driver.c', hidden=False),
            dict(id='test.derived', label='Derived', type='SET_CHOICE', derived=True, source='driver.c', hidden=False)]


def discovery(tmp):
    rows = sweep.derive_rows()
    assert rows and len(rows) == len({r['id'] for r in rows})
    assert 'graphics.brightness' in {r['id'] for r in rows}
    by_id = {r['id']: r for r in rows}
    assert not sweep.valued(by_id['graphics.look_preset'])
    assert sweep.valued(by_id['graphics.brightness'])
    assert sweep.valued(by_id['display.window'])
    # Independent source scan catches a new callback-bearing source omitted by the build parser.
    sources = set(sweep.build_sources(sweep.ROOT))
    actual = set()
    build = (RT / 'build.sh').read_text()
    for path in RT.iterdir():
        if path.suffix in ('.c', '.m', '.mm') and path.name in build:
            text = path.read_text()
            if 'Setting' in text and '.apply' in text and path.name not in {
                'pub_settings.c', 'pub_update.m', 'report_github.m'}:
                actual.add(path.name)
    assert actual <= sources, actual-sources
    fixture = tmp / 'discovery'
    (fixture / 'runtime').mkdir(parents=True)
    (fixture / 'runtime/build.sh').write_text('''if [ "${NFS_PUBLIC:-0}" = 1 ]; then
    public.c
else
    private.c
fi
clang \\
    table.c \\
''')
    (fixture / 'runtime/private.c').write_text('')
    table = '''
typedef int Setting;
#define LIVE(fn) .apply=fn
static Setting a[] = {
 {.id="new.slider", .label="A, {slider}", .type=SET_SLIDER, LIVE(callback)},
 {.id="restart", .type=SET_TOGGLE, .apply=callback, .restart=1},
#ifdef NFS_PUBLIC
 {.id="public", .apply=callback},
#endif
 {.id="command", .type=SET_ACTION, .apply=callback},
 {.id="hidden", .type=SET_TOGGLE, .hidden=1, .apply=callback},
 {.id="derived", .type=SET_CHOICE, .get=summary, .apply=callback, .derived=1},
 {.id="direct", .type=SET_CHOICE, .get=current, .apply=callback, .derived=0}
};
'''
    path = fixture / 'runtime/table.c'
    path.write_text(table)
    found = {r['id']: r for r in sweep.derive_rows(fixture)}
    assert set(found) == {'new.slider', 'command', 'hidden', 'derived', 'direct'}
    assert not sweep.valued(found['derived']) and sweep.valued(found['direct'])
    path.write_text(table.replace('.restart=1', '.restart=0'))
    assert 'restart' in {r['id'] for r in sweep.derive_rows(fixture)}
    path.write_text(table.replace('"new.slider"', 'dynamic_id'))
    try:
        sweep.derive_rows(fixture)
    except ValueError:
        pass
    else:
        raise AssertionError('dynamic live id silently accepted')
    print(f'discovery: {len(rows)} callback rows; new, macro, array, hidden and restart mutations covered')


def main():
    with tempfile.TemporaryDirectory(prefix='live-sweep-test-') as name:
        tmp = Path(name)
        discovery(tmp)
        c = tmp / 'driver.c'
        c.write_text(HARNESS)
        exe = tmp / 'driver'
        built = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                '-fsanitize=address,undefined', '-I', str(RT), str(c),
                                str(RT / 'live_picture_sweep.c'), '-o', str(exe)], capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        rows = inventory()
        count = 0

        def capture(ids, broken=False):
            nonlocal count
            folder = tmp / f'capture-{count}'
            count += 1
            folder.mkdir()
            env = dict(os.environ, NFS_TEST_LIVE_SWEEP_ROWS=','.join(ids),
                       NFS_TEST_LIVE_SWEEP_SKIP_UNDO='graphics.brightness' if broken else '')
            run = subprocess.run([str(exe), '1'], env=env, capture_output=True, text=True, check=True)
            assert 'live sweep: command test.derived' in run.stdout
            assert not any(f'live sweep: {phase} test.derived' in run.stdout for phase in ('init', 'on', 'undo'))
            assert 'Sanitizer' not in run.stderr and 'locked=1' in run.stderr, run.stderr
            assert 'preset_calls=0' in run.stderr, run.stderr
            pixel = int(run.stderr.split('pixel=')[1].split()[0])
            (folder / 'events.log').write_text('IDirect3D9::CreateDevice -> 0\n' + run.stdout)
            Path(str(folder) + '.out').write_text('normal run\n')
            Image.new('RGB', (80, 80), (pixel,)*3).save(folder / 'simshot_018.00.png')
            return folder

        reports = []
        assert sweep.sweep(rows, capture, reports.append), reports
        assert sum(line.startswith('PASS ') for line in reports) == 2, reports
        reports = []
        assert not sweep.sweep(rows, lambda ids: capture(ids, True), reports.append), reports
        assert any(line.startswith('FAIL graphics.brightness ') for line in reports), reports
        # Only the combination fails; both fresh singleton runs pass.
        reports = []
        def interaction(ids):
            return capture(ids, broken=len(ids) > 1)
        assert not sweep.sweep(rows, interaction, reports.append), reports
        assert sum(line.startswith('FAIL batch ') for line in reports) == 1, reports
        assert sum(line.startswith('PASS ') for line in reports) == 2, reports
        print('uncoupled interaction: FAIL despite 2 passing singleton runs')
        base = capture([])
        bad = capture(['graphics.brightness'], True)
        same, metric = sweep.picture(base / 'simshot_018.00.png', bad / 'simshot_018.00.png')
        assert not same and '100.00%' in metric, metric
        print('seed: omitted undo FAIL graphics.brightness; ' + metric)

        good = capture(['graphics.brightness'])
        original = (good / 'events.log').read_text()
        for before, after in [
            ('live sweep: row test.toggle', 'missing row'),
            ('live sweep: undo graphics.brightness', 'missing undo graphics.brightness'),
            ('read=100 want=100', 'read=0 want=100'),
            ('IDirect3D9::CreateDevice -> 0', 'IDirect3DDevice9::Reset -> -1'),
        ]:
            (good / 'events.log').write_text(original.replace(before, after))
            assert sweep.audit(good, rows, ['graphics.brightness']), before
        (good / 'events.log').write_text(original)
        assert not sweep.audit(good, rows, ['graphics.brightness'])
        Path(str(good) + '.out').write_text('boot: restart requested\n')
        assert sweep.audit(good, rows, ['graphics.brightness'])
        image = good / 'simshot_018.00.png'
        Image.new('RGB', (88, 80)).save(image)
        assert not sweep.picture(base / 'simshot_018.00.png', image)[0]
        image.unlink()
        assert not sweep.picture(base / 'simshot_018.00.png', image)[0]
        image.write_bytes(b'broken PNG')
        assert not sweep.picture(base / 'simshot_018.00.png', image)[0]
        for changed, expected in ((3, True), (4, False)):
            pixels = np.full((80, 80, 3), 100, dtype=np.uint8)
            pixels[:8, :changed*8] = 113
            Image.fromarray(pixels).save(image)
            assert sweep.picture(base / 'simshot_018.00.png', image)[0] == expected

        # Failure isolation and budget exhaustion must never turn a failed batch green.
        reports = []
        def expired(ids):
            if ids:
                raise TimeoutError('budget exhausted')
            return base
        assert not sweep.sweep(rows, expired, reports.append)
        assert sum(line.startswith('FAIL ') for line in reports) == 2
        planning_rows = [dict(id=f'test.{i}') for i in range(65)]
        grouped = sweep.batches(planning_rows)
        assert [x for group in grouped for x in group] == planning_rows
        assert max(map(len, grouped)) == 8
        assert (1 + len(grouped))*sweep.RUN_SECONDS < sweep.TOTAL_SECONDS
        # Check the real inventory and both orders without deriving expectations
        # from the planner's coupling table.
        values = [r for r in sweep.derive_rows() if sweep.valued(r)]
        pair = [dict(id='display.fullscreen'), dict(id='display.window')]
        for planned_rows in (values, pair, pair[::-1]):
            for size in (1, 2, sweep.BATCH_SIZE):
                groups = sweep.batches(planned_rows, size)
                assert [r for group in groups for r in group] == planned_rows
                assert max(map(len, groups)) <= size
                shared = sum({'display.fullscreen', 'display.window'} <=
                             {r['id'] for r in group} for group in groups)
                assert shared == 0, f'coupled rows share {shared} batches'
        print('coupled display rows: 0 shared batches; coverage and order preserved')
        # The disabled diagnostic driver does not touch rows or emit events.
        disabled = subprocess.run([str(exe), '0'], capture_output=True, text=True, check=True)
        assert not disabled.stdout and 'pixel=100' in disabled.stderr
        for mode in ('--list', '--plan'):
            run = subprocess.run([str(RT / 'live_picture_check.sh'), mode], capture_output=True, text=True)
            assert run.returncode == 0 and 'value rows with .apply' in run.stdout, run.stdout+run.stderr

        public = tmp / 'public'
        built = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-DNFS_PUBLIC=1',
                                '-I', str(RT), str(c), str(RT / 'live_picture_sweep.c'),
                                '-o', str(public)], capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        run = subprocess.run([str(public), '1'], capture_output=True, text=True, check=True)
        assert not run.stdout and 'pixel=100' in run.stderr

        # Exercise the real launch orchestration with an inert process double, never a game.
        launch_out = tmp / 'launcher'
        launch_out.mkdir()
        seen = []
        class Process:
            def __init__(self, args, **kw):
                seen.append((args, kw))
                self.terminated = False
                self.returncode = None
                folder = Path(args[2])
                folder.mkdir()
                Image.new('RGB', (80, 80)).save(folder / 'simshot_018.00.png')
            def poll(self):
                return self.returncode
            def send_signal(self, sig):
                assert sig == sweep.signal.SIGTERM
                self.terminated = True
            def wait(self, timeout=None):
                assert self.terminated
                self.returncode = 143
                return self.returncode
        with mock.patch.object(sweep.subprocess, 'Popen', Process):
            runner = sweep.Runner(launch_out, rows, 'graphics.brightness')
            result = runner.run(['graphics.brightness'])
            assert result.name == 'run-000'
            args, kw = seen[0]
            assert args[0] == str(RT / 'run_guarded.sh')
            assert f'BOOT_KEYS={sweep.KEYS}' in args and 'BOOT_DIAG_SIMSHOTS=18' in args
            assert 'NFS_TEST_LIVE_SWEEP_ROWS=graphics.brightness' in args
            assert 'NFS_TEST_LIVE_SWEEP_SKIP_UNDO=graphics.brightness' in args
            assert 'NFS_LOCKSTEP=1' in args and 'BOOT_RACE_AI=0' in args
            assert 'BOOT_PAD=sim:xbox@999999' in args and 'BOOT_PAD2=sim:xbox@999999' in args
            assert 'BOOT_KEYS' not in kw['env']
            runner.deadline = sweep.time.monotonic()
            try:
                runner.run(['test.toggle'])
            except TimeoutError:
                pass
            else:
                raise AssertionError('expired runner launched a process')
            assert len(seen) == 1

        # Saved run order must drive the same checker, without any launch on replay.
        replay_out = tmp / 'replay'
        replay_out.mkdir()
        (replay_out / 'inventory.json').write_text(json.dumps(rows))
        records = []
        def recorded(ids):
            folder = capture(ids)
            records.append(dict(folder=str(folder), ids=ids))
            return folder
        assert sweep.sweep(rows, recorded, lambda line: None)
        (replay_out / 'runs.json').write_text(json.dumps(records))
        with mock.patch.object(sweep, 'derive_rows', return_value=rows), mock.patch.object(
                sys, 'argv', ['sweep', 'sweep-logs', str(replay_out)]), mock.patch.object(
                sweep.subprocess, 'Popen', side_effect=AssertionError('replay launched')):
            assert sweep.main() == 0
            (replay_out / 'inventory.json').write_text('[]')
            try:
                sweep.main()
            except ValueError:
                pass
            else:
                raise AssertionError('replay accepted stale inventory')
        print('live_picture_sweep_test: PASS; normal 0.00%, seed 100.00%, all fail-closed controls rejected')


if __name__ == '__main__':
    main()
