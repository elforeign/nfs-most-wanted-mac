"""Loopback load proof; no router traffic, all listeners explicitly 127.0.0.1.

Build production relay at -O2 without sanitizers, plus controls. Normal: exact 8/8/16 rates (5280/2640/1056
entries across 33 clients/second), zero packing overflow and stable interests. Controls:
no hysteresis must increase flaps; no staggering must exceed the 27-entry bundle budget;
linear endpoint lookup must exceed the lookup CPU regression budget and 8 probes/packet.
Recorded controls: membership changes 0/828, packing overflow 0/390; hash/linear probes 1.118/500.500.
Hash deletion/slot reuse and 65,536 joins across id wrap pass; broken chain unlink reports 32 bad ingress packets versus 0.
Reusing a live id across wrap produces 2 bad packets versus 0 and is rejected.
The lookup budget is max(0.1% of one core, 4x measured hash CPU), separate from full-service CPU.
Socket load measures actual 20 Hz ingress and receiver bytes for 8/32/256/1000 clients,
co-located and a 120 m grid. Decimal kB/s/Mbit/s include IPv4+UDP. LOOK is excluded.
Design full-crowd estimates (8/32/1000): 6.900/28.760/29.640 old, 6.900/12.620/12.708
with tiers, plus 5 Hz membership: 0.360/0.960/0.985 kB/s. Max tick includes control work.
"""
from pathlib import Path
import re
import json
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
CF = ['clang', '-O2', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror',
      '-pthread', '-I', str(RT)]


def build(td, name, define=None, relay=None):
    exe = td / name
    cmd = CF + ([f'-D{define}=1'] if define else [])
    p = subprocess.run(cmd + [str(RT / 'roam_load.c'), str(RT / 'roam_core.c'), str(relay or RT / 'roam_relay.c')]
                       + ['-o', str(exe)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-3000:]
    return exe


def run(exe, *args, expected=0):
    p = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=120)
    print(exe.name + ': ' + p.stdout.strip(), flush=True)
    if p.returncode == 3 and 'SOCKET PENDING' in p.stdout:
        return None
    assert p.returncode == expected, (exe.name, p.returncode, p.stdout[-3000:], p.stderr[-3000:])
    return p.stdout


def number(out, key):
    return float(re.search(r'\b' + key + r'=([0-9.]+)', out)[1])


def main():
    report = {'controls': {}, 'loads': []}
    with tempfile.TemporaryDirectory(prefix='roam-load-') as tmp:
        td = Path(tmp)
        normal = build(td, 'normal')
        hysteresis = build(td, 'no_hysteresis', 'ROAM_NO_HYSTERESIS')
        stagger = build(td, 'no_stagger', 'ROAM_NO_STAGGER')
        linear = build(td, 'linear', 'ROAM_LINEAR_LOOKUP')
        good = run(normal, '--fixture')
        bad = run(hysteresis, '--fixture')
        report['controls']['hysteresis'] = {'normal': good, 'broken': bad}
        assert number(bad, 'changes') > number(good, 'changes') + 100, 'hysteresis control did not flap'
        bad = run(stagger, '--fixture', expected=1)
        report['controls']['stagger'] = bad
        assert number(bad.split('TIERS')[1], 'overflow') > 0, 'stagger control did not overflow'
        src = (RT / 'roam_relay.c').read_text()
        old = 'if (*p == slot) *p = k->anext;'
        assert src.count(old) == 1
        broken_unlink = td / 'roam_relay_unlink.c'
        broken_unlink.write_text(src.replace(old, 'if (*p == slot) *p = -1;'))
        bad = run(build(td, 'bad_unlink', relay=broken_unlink), '--fixture', expected=1)
        assert number(bad.split('CHURN')[1], 'bad') > 0, 'hash-chain deletion control did not fail'
        report['controls']['unlink'] = bad
        old = 'while (!r->next_id || r->ids[r->next_id]) r->next_id++;'
        assert src.count(old) == 1
        broken_id = td / 'roam_relay_id.c'
        broken_id.write_text(src.replace(old, 'while (!r->next_id) r->next_id++;'))
        bad = run(build(td, 'bad_id', relay=broken_id), '--fixture', expected=1)
        assert number(bad.split('IDWRAP')[1], 'bad') > 0, 'live-id reuse control did not fail'
        report['controls']['id_wrap'] = bad
        good = run(normal, '--lookup')
        bad = run(linear, '--lookup')
        budget = max(0.1, 4 * number(good, 'cpu_pct'))
        report['controls']['lookup'] = {'normal': good, 'broken': bad, 'budget_pct': budget}
        assert number(good, 'probes_per_packet') <= 8
        assert number(bad, 'probes_per_packet') > 100
        assert number(bad, 'cpu_pct') > budget, ('linear CPU control within budget', budget)
        print(f'lookup CPU budget: {budget:.4f}% of one core; linear exceeds it', flush=True)
        print('players shape down_kB/s relay_Mbit/s CPU_% max_tick_ms design_tier_kB/s', flush=True)
        for count in [8, 32, 256, 1000]:
            for shape in ['crowd', 'spread']:
                out = run(normal, count, shape, 3)
                if out is None:
                    return 3
                report['loads'].append(out.strip())
                # These are service budgets, independent of the ingress lookup microbenchmark.
                assert number(out, 'cpu_pct') < 85, 'relay exceeded 85% of a core'
                assert number(out, 'max_tick_ms') < 50, 'relay missed its 50 ms tick budget'
                assert number(out, 'bad') == number(out, 'errors') == number(out, 'overflow') == 0
                if shape == 'crowd':
                    # 20 Hz * (IPv4/UDP + header/count), then tiered entries and 5 Hz membership.
                    interests = min(count - 1, 32)
                    near = min(interests, 8)
                    mid = min(max(interests - 8, 0), 8)
                    far = max(interests - 16, 0)
                    design = (20 * 37 + 44 * (near * 20 + mid * 10 + far * 2)
                              + 5 * (37 + interests * 5)) / 1000
                    actual = number(out, 'down_kBps')
                    assert 0.90 * design <= actual <= 1.08 * design, (count, actual, design)
                else:
                    design = None
                print(f'{count:4} {shape:6} {number(out,"down_kBps"):7.3f} '
                      f'{number(out,"out_Mbps"):8.3f} {number(out,"cpu_pct"):6.2f} '
                      f'{number(out,"max_tick_ms"):7.3f} {design if design else "spatial"}', flush=True)
    # Keep requested benchmark evidence alongside the suite logs (the suite removes its temporary driver logs).
    dest = RT.parent / '.metal-cache' / 'roam-load-latest.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2) + '\n')
    print('ROAM LOAD PASS: eight loopback loads, exact ingress, tier budgets and five controls; report', dest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
