#!/usr/bin/env python3
"""




















"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def records(path):
    """Retain only the small passive snapshot stream, preserving source lines."""
    result = []
    race = 0
    in_fe = True
    with path.open(errors='replace') as stream:
        for line, raw in enumerate(stream, 1):
            if 'splitflow:' in raw or 'split lifecycle: restart invalidated' in raw or 'scripted pad2:128 down' in raw:
                fields = dict(re.findall(r'(\w+)=([^\s]+)', raw))
                if 'splitflow: state ' in raw:
                    if fields.get('flow') == '3':
                        in_fe = True
                    elif fields.get('flow') == '6' and in_fe:
                        race += 1
                        in_fe = False
                fields['race'] = str(race)
                result.append((line, raw.rstrip(), fields))
    return result


class MissingStage(Exception):
    pass


def check(path, winner, emit=print):
    path = Path(path)
    if path.is_dir():
        path = path / 'diag/events.log'
    rows = records(path) if path.is_file() else []
    cursor = 0
    passed = 0
    stage = 'fe'
    phase = 0

    def seek(predicate):
        nonlocal cursor
        for i in range(cursor, len(rows)):
            if predicate(rows[i]):
                cursor = i + 1
                return rows[i]
        raise MissingStage

    def state(**values):
        return lambda row: 'splitflow: state ' in row[1] and row[2]['race'] == str(phase) and all(row[2].get(k) == str(v) for k, v in values.items())

    def positive(row, key):
        value = row[2].get(key, '')
        return value.isdecimal() and int(value) > 0

    def player(slot, done, rank=None):
        return lambda row: row[2]['race'] == str(phase) and row[2].get('player') == str(slot) and row[2].get('finished') == str(done) and \
            re.fullmatch(r'[0-9A-Fa-f]{8}', row[2].get('racer', '')) and int(row[2]['racer'], 16) != 0 and \
            (rank is None or row[2].get('rank') == str(rank)) and \
            (not done or (row[2].get('progress') == '100.0000' and row[2].get('flags') == '00/00/00/00'))

    def reached(row):
        nonlocal passed
        passed += 1
        emit(f'{stage}=1 {path}:{row[0]} {row[1]}')

    def pair():
        first = seek(lambda r: player(1, 0)(r) or player(2, 0)(r))
        second = seek(player(3-int(first[2]['player']), 0))
        if first[2]['racer'] == second[2]['racer']:
            raise MissingStage
        return second, first if first[2]['player'] == '2' else second

    try:
        baseline = seek(lambda r: state(flow=3, package='MainMenu.fng', input=1, captures=0)(r) and r[2].get('handles', '').isdecimal())
        handles = baseline[2]['handles']
        reached(baseline)
        stage = 'split-race'
        phase = 1
        row = seek(lambda r: state(flow=6, sim=3, split=1, locals=2, views=2)(r) and positive(r, 'captures'))
        row, p2 = pair(); reached(row)
        stage = 'p2-audio'
        if not positive(p2, 'engines') or not positive(p2, 'voices'):
            raise MissingStage
        reached(p2)
        stage = 'p2-pause'
        seek(lambda r: r[2]['race'] == str(phase) and 'scripted pad2:128 down' in r[1])
        reached(seek(state(flow=6, sim=4, package='Pause_Main.fng')))
        stage = 'resume'
        reached(seek(state(flow=6, sim=3, package='none')))
        for slot, rank in ((winner, 1), (3-winner, 2)):
            stage = f'p{slot}-finish'
            reached(seek(player(slot, 1, rank)))
        stage = 'results'
        reached(seek(state(package='PostRace_Results.fng', input=1)))
        stage = 'restart'
        reached(seek(lambda r: r[2]['race'] == str(phase) and 'split lifecycle: restart invalidated both capture and post banks' in r[1]))
        stage = 'restart-race'
        seek(state(flow=6, sim=3, split=1, locals=2, views=2))
        reached(pair()[0])
        stage = 'quit-fe'
        reached(seek(state(flow=3, package='MainMenu.fng', input=1, captures=0, handles=handles, locals=0, views=0)))
        stage = 'single-race'
        phase = 2
        seek(state(flow=6, sim=3, split=0, locals=1, views=0, captures=0))
        reached(seek(player(1, 0)))
        stage = 'single-quit-fe'
        reached(seek(state(flow=3, package='MainMenu.fng', input=1, captures=0, handles=handles, locals=0, views=0)))
        stage = 'split-again'
        phase = 3
        seek(lambda r: state(flow=6, sim=3, split=1, locals=2, views=2)(r) and positive(r, 'captures'))
        reached(pair()[0])
        stage = 'final-fe'
        reached(seek(state(flow=3, package='MainMenu.fng', input=1, captures=0, handles=handles, locals=0, views=0)))
    except MissingStage:
        line = rows[cursor-1] if cursor else (0, '(no passive evidence)', {})
        emit(f'{stage}=0 {path}:{line[0]} last reached: {line[1]}')
        emit(f'SPLITFLOW FAIL {passed}/15 first missing: {stage}')
        return False
    emit(f'SPLITFLOW PASS {passed}/15 winner=P{winner}; audible P2 pending')
    return True


def settings(winner, broken=False):
    # Sprint avoids a multi-lap acceptance run. Car scrolling waits for the
    # retail selection guard before accepting either player.
    seq = ['skipmovies', 'pkg:MainMenu.fng:RETURN', 'wait:90', 'RIGHT', 'wait:20', 'RIGHT', 'wait:20',
           'RETURN', 'wait:90', 'RIGHT', 'wait:20', 'RIGHT', 'wait:20', 'RETURN', 'wait:90', 'RIGHT', 'wait:20', 'RETURN',
           'pkg:Track_Select.fng', 'wait:60', 'RETURN', 'pkg:Track_Options.fng', 'wait:60', 'RETURN']
    for slot in (1, 2):
        pad = 'pad' if slot == 1 else 'pad2'
        seq += ['pkg:PressStart.fng', 'wait:60', pad+':128', 'wait:60', 'pkg:Car_Select.fng', 'wait:60']
        if slot == winner:
            seq += [pad+'axis:lx=1:6', 'wait:20', pad+'axis:lx=1:6', 'wait:120']
        seq += [pad+':1', 'wait:90', pad+':1']
    seq += ['pad2wait:split', 'wait:120', 'pad2:128', 'wait:60', 'shot', 'pad2:128', 'pad2wait:finished',
            'pkg:PostRace_Results.fng:RETURN', 'pad2wait:results', 'wait:120', 'shot']
    seq += ['1', 'pkg:InGameDialog.fng', 'wait:60', 'shot', 'LEFT', 'wait:20', 'RETURN', 'pad2wait:split', 'wait:120']
    def quit_fe(skip_confirm=False):
        # Pause remembers its last icon; clamp to Resume, then select Quit (2).
        return ['ESCAPE', 'pkg:Pause_Main.fng', 'wait:90'] + ['LEFT', 'wait:20']*4 + ['RIGHT', 'wait:30', 'RIGHT', 'wait:30', 'RETURN',
                'pkg:InGameDialog.fng', 'wait:60', 'LEFT', 'wait:20'] + ([] if skip_confirm else ['RETURN']) + [
                'pkg:MainMenu.fng:ESCAPE', 'wait:60', 'shot']
    seq += quit_fe()
    seq += ['RETURN', 'wait:90', 'LEFT', 'wait:20', 'LEFT', 'wait:20', 'RETURN',
            'pkg:Quick_Race_Brief.fng', 'wait:120', 'RETURN', 'pad2wait:solo', 'wait:120', 'shot']
    seq += quit_fe()
    seq += ['RETURN', 'wait:90', 'RIGHT', 'wait:20', 'RIGHT', 'wait:20', 'RETURN', 'wait:90', 'RETURN',
            'pkg:Track_Select.fng', 'wait:60', 'RETURN', 'pkg:Track_Options.fng', 'wait:60', 'RETURN']
    for pad in ('pad', 'pad2'):
        seq += ['pkg:PressStart.fng', 'wait:60', pad+':128', 'wait:60', 'pkg:Car_Select.fng',
                'wait:120', pad+':1', 'wait:90', pad+':1']
    seq += ['pad2wait:split', 'wait:120', 'shot'] + quit_fe(broken)
    seq += ['wait:120', 'pad2wait:quit']
    assert all(len(token) <= 31 for token in seq)
    return ['NFS_PADS=2', 'BOOT_PAD=sim:dualsense', 'BOOT_PAD2=sim:dualsense,ry=0.8,rt=1',
            'BOOT_AUTODRIVE=2', 'BOOT_RACE=0', 'BOOT_REG=g_RacingResolution=0', 'BOOT_KEYS_STRICT=1',
            'BOOT_KEYS=seq:' + ','.join(seq)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', type=Path)
    mode.add_argument('--run', type=Path)
    mode.add_argument('--settings', action='store_true')
    parser.add_argument('--winner', type=int, choices=(1, 2))
    parser.add_argument('--broken', action='store_true')
    parser.add_argument('--seconds', type=int, default=300)
    args = parser.parse_args()
    if not args.run and args.winner is None:
        parser.error('--check and --settings require --winner')
    if args.settings:
        print(json.dumps(settings(args.winner, args.broken)))
        return 0
    if args.run:
        if args.seconds < 10:
            parser.error('--seconds must be at least 10')
        args.run = args.run.resolve()
        if args.run.exists() and any(args.run.iterdir()):
            parser.error('--run requires a fresh scratch directory')
        for winner in (args.winner,) if args.winner else (1, 2):
            out = args.run if args.winner else args.run/f'p{winner}'
            out.mkdir(parents=True, exist_ok=True)
            print(f'case: P{winner} first', flush=True)
            with (out / 'guard.out').open('w') as log:
                result = subprocess.run([str(ROOT/'runtime/run_guarded.sh'), str(args.seconds), str(out/'diag'),
                                         *settings(winner, args.broken)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            # Always report the first missing stage, even on a guard failure.
            good = check(out/'diag/events.log', winner)
            if not good or result.returncode:
                return 1
        return 0
    return 0 if check(args.check, args.winner) else 1


if __name__ == '__main__':
    raise SystemExit(main())
