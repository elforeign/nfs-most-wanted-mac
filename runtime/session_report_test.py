"""runtime/session_report.py: P13 race -> P1 slowdown verdict, no game/assets.

Evidence fixture: 1152/1155 fast seconds at P13 (9.25 ms), 298/357 slow
seconds at P1 (28.7 ms). Fixed parser passes; old-parser control fails
the identical verdict assertion (exit 1). MHz/unknown labels remain separate.
Slowlog evidence: six slow seconds, one AC -> battery episode, 6 ms wait
total / 0.75 ms maximum; missing-new-columns mutant fails the same assertion
(exit 1). Repeated IDs merge wait deltas; legacy logs work.
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


def slowlog_evidence(module):
    rows = []
    for t in range(60):
        slow = 40 <= t < 46
        rows.append(dict(t=t, state='race', gpu_mean=24 if slow else 7, draws=3000, fps=30 if slow else 120,
                         busy_p50=4, thermal=1, low_power=0, app_active=1, occluded=0, fullscreen=1,
                         gpu_wall=25 if slow else 8, gpu_other_ms=2, gpu_top='test:2', gpu_mhz='state=P1' if slow else 'state=P13',
                         slowlog_enabled=1, slowlog_sample=str(t+1), slowlog_dt=1, slowlog_age_ms=200,
                         post_path='X360', lum_wait_sum_ms=1, lum_wait_max_ms=0.75, lum_wait_count=3, lum_wait_dropped=0,
                         power_source='AC' if t<43 else 'battery', battery_pct=70 if t<43 else 69,
                         gpu_pstates='IDLE:1.00,P1:99.00', gpu_clock_status='decoded_residency'))
    output = StringIO()
    with redirect_stdout(output):
        module['gpu_slow_stretches'](rows, lambda row, key: float(row[key]))
    text = output.getvalue()
    for value in ('post_path X360', 'slowlog_enabled 1', 'slowlog_sample 6 unique', 'slowlog_dt 1.000-1.000 s',
                  'slowlog_age_ms max 200.0', 'lum_wait_sum_ms total 6.000', 'lum_wait_max_ms 0.750',
                  'lum_wait_count 18', 'lum_wait_dropped 0', 'gpu_pstates IDLE:1.00,P1:99.00',
                  'gpu_clock_status decoded_residency', 'power-source: AC -> battery',
                  'battery_pct 70.0 -> 69.0%', 'thermal max 1, low_power 0', 'gpu_other_ms mean 2.0'):
        assert value in text, value + ': ' + text
    assert 'lum_wait_sum_ms total 2.000' in module['slowlog_evidence']([rows[40], rows[40]])
    old = [{k:v for k,v in row.items() if k not in ('lum_wait_sum_ms', 'power_source')} for row in rows]
    assert 'no luminance/power-source columns' in module['slowlog_evidence'](old)
    assert 'no power_source/battery_pct columns' in module['power_episode'](old)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--check':
        evidence(runpy.run_path(sys.argv[2]))
        return
    if len(sys.argv) == 3 and sys.argv[1] == '--slowlog-check':
        slowlog_evidence(runpy.run_path(sys.argv[2]))
        return
    source = (RT / 'session_report.py').read_text()
    module = runpy.run_path(str(RT / 'session_report.py'))
    evidence(module)
    slowlog_evidence(module)
    print('PASS slowlog stretch: 6 ms total / 0.75 ms maximum, AC -> battery, charge 70 -> 69%, 6 unique samples')
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
        old = ' + slowlog_evidence(g)'
        assert source.count(old) == 1
        mutant.write_text(source.replace(old, ''))
        p = subprocess.run([sys.executable, '-I', __file__, '--slowlog-check', str(mutant)], capture_output=True, text=True)
        assert p.returncode == 1 and 'AssertionError' in p.stderr, p.stdout + p.stderr[-2000:]
        print('PASS missing slowlog output control: same evidence assertion fails (exit 1)')


if __name__ == '__main__':
    main()
