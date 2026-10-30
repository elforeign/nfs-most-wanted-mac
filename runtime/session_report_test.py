"""P-state regression: P13 race -> P1 slowdown verdict, no game/assets.

Evidence fixture: 1152/1155 fast seconds at P13 (9.25 ms), 298/357 slow
seconds at P1 (28.7 ms). Fixed parser passes; old-parser control fails
the identical verdict assertion (exit 1). MHz/unknown labels remain separate.
"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

RT = Path(__file__).resolve().parent


def evidence(module):
    rows = []
    for t in range(1512):
        slow = t >= 1155
        rows.append(dict(t=t, state='race', gpu_mean=28.7 if slow else 9.25,
                         draws=4000, busy_p50=9.9, fps=31 if slow else 89,
                         gpu_other_ms=0, gpu_top='-',
                         gpu_mhz=('state=P1' if t - 1155 < 298 else 'state=P13') if slow
                         else ('state=P13' if t < 1152 else '-')))
    output = StringIO()
    with redirect_stdout(output):
        module['gpu_slow_stretches'](rows, lambda row, key: float(row[key]))
    text = output.getvalue()
    assert 'verdict GPU clock dropped (P13 -> P1)' in text, text
    assert 'gpu_state median P1 vs race median P13' in text, text
    assert '(357 s): gpu 28.7 ms (x3.1 of 9.2)' in text, text
    assert 'gpu_mhz mean - vs race median -' in text, text


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--check':
        evidence(runpy.run_path(sys.argv[2]))
        return
    source = (RT / 'session_report.py').read_text()
    module = runpy.run_path(str(RT / 'session_report.py'))
    evidence(module)
    print('PASS P-state evidence verdict: 1512 race seconds, 357-second slowdown, P13 -> P1')
    parse, cause = module['gpu_pstate'], module['gpu_cause']
    for value in ('-', '', 'state=IDLE', 'state=P1junk', 'state=500', '500', 'NaN', 'state=P-1'):
        assert parse({'gpu_mhz': value}) is None, value
    assert parse({'gpu_mhz': 'state=P13'}) == 13
    assert parse({'gpu_mhz': 'state=P0'}) == 0
    f = lambda row, key: float(row[key])
    row = {'gpu_mhz': 'state=P1', 'gpu_mean': 28.7, 'fps': 31, 'gpu_other_ms': 0}
    assert 'verdict undecided' in cause([(row, 9.25)], None, f)
    for race in (0, 1):
        assert 'verdict undecided' in cause([(row, 9.25)], None, f, race)
    assert 'GPU clock dropped (P13 -> P0)' in cause([({**row, 'gpu_mhz': 'state=P0'}, 9.25)], None, f, 13)
    assert 'verdict contention' in cause([({**row, 'gpu_other_ms': 600}, 9.25)], None, f, 13)
    assert 'verdict clock' in cause([({**row, 'gpu_mhz': '500'}, 9.25)], 1000, f)
    assert 'verdict undecided' in cause([({**row, 'gpu_mhz': '-'}, 9.25)], None, f, 13)
    print('PASS 10 parser cases and 7 missing/unchanged/rising/P0/contention/MHz controls')
    with tempfile.TemporaryDirectory(prefix='session-report-test-') as scratch:
        mutant = Path(scratch) / 'old_parser.py'
        old = 'return int(match.group(1)) if match else None'
        assert source.count(old) == 1
        mutant.write_text(source.replace(old, 'return None'))
        p = subprocess.run([sys.executable, '-I', __file__, '--check', str(mutant)], capture_output=True, text=True)
        assert p.returncode == 1 and 'AssertionError:' in p.stderr and 'verdict undecided' in p.stderr, p.stdout + p.stderr[-2000:]
        print('PASS old-parser regression control: same evidence assertion fails (exit 1)')


if __name__ == '__main__':
    main()
