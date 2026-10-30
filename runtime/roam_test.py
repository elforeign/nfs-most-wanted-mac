"""Online free roam core + relay over 127.0.0.1 (runtime/roam_test.c), and its broken control.

normal: roam_core.c as shipped; must print ROAM PASS (exit 0).
broken: a generated copy whose render clock follows the newest packet's delay instead of the least-delayed one
        (the jitter-buffer clock bug); must fail on the lag check (exit 1).
Recorded 2026-10-29: normal lag 150.0..150.0 ms (spread 0.0), off-path max 0.0128 m, orientation max 0.92 deg;
broken lag 150.0..265.0 ms (spread 115.0) -> FAIL.
blend: the car-appear discontinuity removed (snapshots without a car at the origin, then the car 2 km away): normal
       worst blended pose 0.0 m; blend 1790.9 m -> FAIL on the appear check. Socket EPERM -> SOCKET PENDING, exit 3.
Membership/map/order/duplicate-age controls: normal exit 0, each mutant exit 1 on its named check.
Stale-state control: normal removes a LOOK-only peer within 950 ms; freshness mutant retains it and exits 1.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
RT = ROOT / 'runtime'
MIN_LOOP = 'for (int i = 1; i < r->noff; ++i) if (r->off[i] < off) off = r->off[i];'
BROKEN = 'off = r->off[(r->ioff + ROAM_OFFS - 1) % ROAM_OFFS];'
APPEAR = 'if (r->n && ((s->flags ^ r->s[r->n - 1].flags) & ROAM_FLAG_CAR)) {'
CF = ['clang', '-std=c11', '-D_DEFAULT_SOURCE', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g',
      '-fsanitize=address,undefined', '-I', str(RT)]


def run(td, name, core, relay=None, args=()):
    exe = Path(td) / ('roam_' + name)
    p = subprocess.run(CF + [str(RT / 'roam_test.c'), str(core), str(relay or RT / 'roam_relay.c'), '-o', str(exe)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2500:]
    p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=120)
    for line in p.stdout.splitlines():
        if line.startswith(('interp:', 'FAIL', 'ROAM', 'SOCKET', 'scale:', 'rate:', 'interest', 'timeout:', 'version:', 'appear:', 'membership:')):
            print(name + ':', line, flush=True)
    return p.returncode, p.stdout + p.stderr


def main():
    src = (RT / 'roam_core.c').read_text()
    assert src.count(MIN_LOOP) == 1, 'broken-control anchor moved'
    with tempfile.TemporaryDirectory(prefix='roam-') as td:
        broken = Path(td) / 'roam_core_broken.c'
        broken.write_text(src.replace(MIN_LOOP, BROKEN))
        rc, out = run(td, 'normal', RT / 'roam_core.c')
        if rc == 3 and 'SOCKET PENDING' in out:
            print('SOCKET PENDING: run on a socket-enabled host (roam_test)')
            return 3
        assert rc == 0, out[-3000:]
        rc, out = run(td, 'broken', broken)
        assert rc == 1 and 'FAIL: lag' in out, ('broken control did not fail on the lag check', rc, out[-3000:])
        assert src.count(APPEAR) == 1, 'appear-control anchor moved'
        broken2 = Path(td) / 'roam_core_blend.c'
        broken2.write_text(src.replace(APPEAR, 'if (0) {'))
        rc, out = run(td, 'blend', broken2)
        assert rc == 1 and 'FAIL: appear' in out, ('blend control did not fail on the appear check', rc, out[-3000:])
        mutations = {
            'eviction': ('if (at < 0) c->view.r[i].used = 0;', 'if (at < 0) (void)0;', 'resurrected entry'),
            'map': ('r->map_only || ', '', 'map tier became drawable'),
            'member_order': ('if (c->has_members && (int16_t)(uint16_t)(seq - c->member_seq) <= 0) return;',
                             '', 'resurrected entry'),
            'duplicate_age': ('if (!r->n || (int32_t)(s->t - r->s[r->n - 1].t) > 0) r->last_rx = local_ms;',
                              'r->last_rx = local_ms;', 'duplicate refreshed pose'),
        }
        for name, (old, new, failure) in mutations.items():
            mutant_src = src
            if name == 'duplicate_age':
                # Restore the original defect: refresh before deduplication.
                mutant_src = mutant_src.replace('if (!r) return;\n    for (int i = 0; i < r->n;',
                                                'if (!r) return;\n    r->last_rx = local_ms;\n    for (int i = 0; i < r->n;')
            assert mutant_src.count(old) == 1, (name, 'control anchor moved')
            mutant = Path(td) / f'roam_core_{name}.c'
            mutant.write_text(mutant_src.replace(old, new))
            rc, out = run(td, name, mutant, args=('--membership-only',))
            assert rc == 1 and failure in out, (name, rc, out[-2500:])
        relay_src = (RT / 'roam_relay.c').read_text()
        old = '(uint32_t)(now - k->state_ms) <= ROAM_STATE_FRESH_MS'
        assert relay_src.count(old) == 1
        stale = Path(td) / 'roam_relay_stale.c'
        stale.write_text(relay_src.replace(old, '((void)now, 1)'))
        rc, out = run(td, 'stale', RT / 'roam_core.c', relay=stale)
        assert rc == 1 and 'stale state retained by LOOK heartbeat' in out, (rc, out[-2500:])
        # The standalone relay builds and runs (loopback, an ephemeral port, 1 s).
        cli = Path(td) / 'roam_relay'
        p = subprocess.run(CF + [str(RT / 'roam_relay_main.c'), str(RT / 'roam_core.c'), str(RT / 'roam_relay.c'),
                                 '-o', str(cli)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-2500:]
        p = subprocess.run([str(cli), '--port', '0', '--seconds', '1'], capture_output=True, text=True, timeout=20)
        print('cli:', p.stdout.strip().splitlines()[:1], 'exit', p.returncode, flush=True)
        assert p.returncode == 0 and 'listening on 127.0.0.1:0' in p.stdout, p.stdout + p.stderr
    print('ROAM TEST PASS: normal + seven clock/appearance/membership/freshness mutants rejected; relay CLI runs')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
