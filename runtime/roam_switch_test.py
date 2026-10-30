"""Production game-hook session reset, legacy env switches and zero-work Off path; fake sockets/guest calls.
Proof: normal 71 checks, 0 failures; broken Off 2, front-end cleanup 6, timeout 1,
pre-distance pose guard 33, diagnostic guard 1 failures; sockets 4/4, guest calls 0.
Restored connect/input error loggers each leak one IPv4 and are rejected; normal zero leaks.
Broken Off path polls the session controller every frame.
"""
from pathlib import Path
import subprocess
import tempfile
import re
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g',
      '-fsanitize=address,undefined', '-I', str(RT), '-I', str(RT.parent / 'recomp')]


def main():
    assert re.search(r'^00663D30\s+roam_game\.c:',
                     (RT.parent / 'recomp/hooks.txt').read_text(), re.M), 'front-end session hook is unregistered'
    source = (RT / 'roam_game.c').read_text()
    old = 'if (state < 0) return;'
    assert source.count(old) == 1
    distance = 'static float roam_dist_local(const float p[3]) {'
    assert source.count(distance) == 1
    source = source.replace(distance, distance + ' ++distance_calls;')
    harness = (RT / 'roam_switch_test.c').read_text()
    with tempfile.TemporaryDirectory(prefix='roam-switch-') as tmp:
        td = Path(tmp)
        timeout = 'if (roam.welcomed && (uint32_t)(now - roam_last_rx) > 5000)'
        assert source.count(timeout) == 1
        front_end = 'void f_00663D30(Cpu *c) {\n    roam_session(c);'
        assert source.count(front_end) == 1
        for label, text in [('normal', source), ('broken_off', source.replace(old, 'if (state < 0) atomic_store(&roam_state, 2);')),
                            ('broken_front_end', source.replace(front_end, 'void f_00663D30(Cpu *c) {')),
                            ('broken_log', source.replace('"roam: bind/connect: %s\\n", strerror(errno)', '"roam: bind/connect %s: %s\\n", v, strerror(errno)')),
                            ('broken_input_log', source.replace('"roam: NFS_ROAM is not ip:port; online free roam off\\n"', '"roam: NFS_ROAM=%s is not ip:port; online free roam off\\n", v')),
                            ('broken_diag', source.replace('if (diag_on) diag_event("roam: on, relay', 'diag_event("roam: on, relay')),
                            ('broken_timeout', source.replace(timeout, 'if (0)')),
                            ('broken_pose', source.replace('if (!roam_pose_valid(pos, vel)) continue;', ''))]:
            src = td / (label + '.c'); src.write_text(text)
            test = td / (label + '_harness.c'); test.write_text(harness.replace('"roam_game.c"', '"' + str(src) + '"'))
            exe = td / label
            p = subprocess.run(CF + [str(test), str(RT / 'roam_core.c'), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-3000:]
            p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            print(label + ':', p.stdout.strip(), flush=True)
            leaks = re.findall(r'(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])', p.stderr)
            if label in ('broken_log', 'broken_input_log'):
                assert p.returncode == 0 and leaks, 'endpoint logger control escaped detection'
                print(label + ': rejected %d leaked addresses' % len(leaks), flush=True)
            else:
                assert not leaks, 'IPv4 leaked in ordinary game-hook logs'
                assert p.returncode == (0 if label == 'normal' else 1), p.stderr[-2500:]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
