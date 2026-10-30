"""State ingress proof, ASan/UBSan, no network I/O. Proof: normal 152 checks, 0 failures; unchecked relay 48 failures; unchecked client 90.
Relay guard removed = original unchecked STATE storage; view guard removed = original
malicious-relay path. Invalid replacement must preserve a previously valid snapshot.
"""
from pathlib import Path
import subprocess
import tempfile
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g',
      '-fsanitize=address,undefined', '-I', str(RT)]


def main():
    mutants = {
        'relay': ('roam_relay.c', 'if (!roam_pose_valid(state.pos, state.vel)) { r->st.rx_bad++; return; }', ''),
        'client': ('roam_core.c', 'if (!roam_pose_valid(s->pos, s->vel)) return;', ''),
    }
    with tempfile.TemporaryDirectory(prefix='roam-state-') as tmp:
        td = Path(tmp)
        for name in ['normal', *mutants]:
            sources = {f: RT / f for f in ['roam_core.c', 'roam_relay.c']}
            if name in mutants:
                file, old, new = mutants[name]; src = sources[file].read_text(); assert src.count(old) == 1
                out = td / file; out.write_text(src.replace(old, new)); sources[file] = out
            exe = td / name
            p = subprocess.run(CF + [str(RT / 'roam_state_test.c'), *map(str, sources.values()), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-2500:]
            p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            print(name + ': ' + p.stdout.strip(), flush=True)
            assert p.returncode == (0 if name == 'normal' else 1), p.stderr[-2500:]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
