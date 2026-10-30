"""Online join-code, router wire fixtures and embedded relay, with production-source mutants.

Proof (normal failures / deliberate broken failures): code 0/256003 (530441 good checks); checksum omitted 18355 failures (530441 checks),
NAT-PMP 0/1 (9), PCP 0/1 (9), UPnP 0/1 (14), relay 0/1 (2 pure checks).
Relay pure transmissions 8/4; socket lifecycle passes in the trusted whole offline suite.
No router calls; relay lifecycle binds only 127.0.0.1. EPERM/EACCES = PENDING host run (3).
RFC 6886 sections 3/3.1/3.3 and RFC 6887 sections 7.1/11.1 specify the byte fixture layouts.
"""
from pathlib import Path
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
SOURCES = ['roam_online_test.c', 'roam_code.c', 'natpmp.c', 'pcp.c', 'upnp.c', 'roam_server.c', 'roam_core.c', 'roam_relay.c']
MUTANTS = {
    'code': ('roam_code.c', '((uint64_t)ip << 8)', '((uint64_t)ip << 7)'),
    'natpmp': ('natpmp.c', 'b[1] = 1;', 'b[1] = 2;'),
    'pcp': ('pcp.c', 'b[36] = 17;', 'b[36] = 6;'),
    'upnp': ('upnp.c', '!strncasecmp(p, key, k)', '!strncmp(p, key, k)'),
    'checksum': ('roam_code.c', 'count != 9 || check ||', 'count != 9 ||'),
    'relay': ('roam_relay.c', 'k->has_state = 1;', 'k->has_state = 0;'),
}
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g',
      '-fsanitize=address,undefined', '-pthread', '-I', str(RT)]


def build(td, label, replace=None):
    src = [str(RT / f) for f in SOURCES]
    if replace:
        filename, old, new = replace
        text = (RT / filename).read_text()
        assert text.count(old) == 1, (filename, old, 'mutant anchor moved')
        mutant = td / filename
        mutant.write_text(text.replace(old, new))
        src[SOURCES.index(filename)] = str(mutant)
    exe = td / label
    p = subprocess.run(CF + src + ['-o', str(exe)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-3000:]
    return exe


def main():
    pending = False
    with tempfile.TemporaryDirectory(prefix='roam-online-') as tmp:
        td = Path(tmp)
        normal = build(td, 'normal')
        for case, mutant in MUTANTS.items():
            test_case = "code" if case == "checksum" else case
            p = subprocess.run([str(normal), test_case], capture_output=True, text=True, timeout=30)
            print('normal:', p.stdout.strip(), flush=True)
            if p.returncode == 3 and 'PENDING host run' in p.stdout:
                pending = True
            else:
                assert p.returncode == 0, p.stderr[-2000:]
            broken = build(td, 'broken_' + case, mutant)
            q = subprocess.run([str(broken), test_case], capture_output=True, text=True, timeout=30)
            print('broken:', q.stdout.strip(), flush=True)
            assert q.returncode == 1 and 'FAIL line' in q.stdout, (case, q.returncode, q.stderr[-2000:])
    print('ONLINE CODECS PASS; all 6 deliberate mutants rejected' + ('; sockets PENDING host run' if pending else '; embedded relay PASS'))
    return 3 if pending else 0


if __name__ == '__main__':
    raise SystemExit(main())
