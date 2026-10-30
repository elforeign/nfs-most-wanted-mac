"""Loopback visual peer smoke check; no game launch or router traffic.
Proof: normal welcomed=1/exit 0; HELLO removed welcomed=0/exit 1 (two seconds each).
The same fixture supplies the synthetic remote car in guarded screenshot runs.
"""
from pathlib import Path
import subprocess
import tempfile
RT = Path(__file__).resolve().parent


def main():
    source = (RT / 'roam_loopback_fixture.c').read_text()
    hello = 'if (!friend.welcomed || !(now%1000/50))'
    assert source.count(hello) == 1
    with tempfile.TemporaryDirectory(prefix='roam-visual-') as tmp:
        td = Path(tmp)
        for label, text in [('normal', source), ('broken', source.replace(hello, 'if (0)'))]:
            src = td / (label + '.c'); src.write_text(text)
            exe = td / label
            p = subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-g', '-pthread',
                                '-fsanitize=address,undefined', '-I', str(RT), str(src),
                                str(RT/'roam_core.c'), str(RT/'roam_relay.c'), '-o', str(exe)],
                               capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-2000:]
            p = subprocess.run([str(exe), '2', '0'], capture_output=True, text=True, timeout=30)
            print(label + ': ' + p.stdout.strip(), flush=True)
            if p.returncode == 3 and ('Operation not permitted' in p.stderr or 'Permission denied' in p.stderr):
                return 3
            assert p.returncode == (0 if label == 'normal' else 1), p.stderr[-2000:]
            assert ('welcomed=1' if label == 'normal' else 'welcomed=0') in p.stdout
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
