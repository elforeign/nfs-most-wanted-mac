"""











"""
from pathlib import Path
import subprocess
import sys
import tempfile

RT = Path(__file__).resolve().parent
SOURCES = ['roam_online_test.c', 'roam_code.c', 'natpmp.c', 'pcp.c', 'upnp.c', 'roam_server.c', 'roam_core.c', 'roam_relay.c', 'roam_auth.c', 'roam_moderation.c']
MUTANTS = {
    'code': ('roam_code.c', '((uint64_t)ROAM_CODE_VERSION << 32) | room', '((uint64_t)room << 8)' ),
    'natpmp': ('natpmp.c', 'b[1] = 1;', 'b[1] = 2;'),
    'pcp': ('pcp.c', 'b[36] = 17;', 'b[36] = 6;'),
    'upnp': ('upnp.c', '!strncasecmp(p, key, k)', '!strncmp(p, key, k)'),
    'checksum': ('roam_code.c', 'count != 9 || check ||', 'count != 9 ||'),
    'prefix': ('roam_code.c', "toupper((unsigned char)code[0]) != 'R'", '0'),
    'version': ('roam_code.c', '(v >> 32) != ROAM_CODE_VERSION', '0'),
    'relay': ('roam_relay.c', 'k->has_state = 1;', 'k->has_state = 0;'),
}
# Guarantee a scheduling gap longer than one receive poll, even on an idle host.
DELAYED_RELAY = ('roam_server.c', 'poll(&p, 1, 10);',
                 'poll(&p, 1, 10); struct timespec delay = {.tv_nsec = 150000000}; nanosleep(&delay, NULL);')
CF = ['clang', '-std=c11', '-D_DEFAULT_SOURCE', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g',
      '-fsanitize=address,undefined', '-pthread', '-I', str(RT)]
LINK = ['-framework','Security','-framework','CoreFoundation'] if sys.platform == 'darwin' else ['-lcrypto', '-lm']


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
    p = subprocess.run(CF + src + ['-o', str(exe)] + LINK, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-3000:]
    return exe


def main():
    pending = False
    with tempfile.TemporaryDirectory(prefix='roam-online-') as tmp:
        td = Path(tmp)
        normal = build(td, 'normal')
        for case, mutant in MUTANTS.items():
            test_case = "code" if case in ("checksum", "prefix", "version") else case
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
        delayed = build(td, 'delayed_relay', DELAYED_RELAY)
        p = subprocess.run([str(delayed), 'relay'], capture_output=True, text=True, timeout=30)
        print('delayed relay:', p.stdout.strip(), flush=True)
        if p.returncode == 3 and 'PENDING host run' in p.stdout:
            pending = True
        else:
            assert p.returncode == 0, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    print('ONLINE CODECS PASS; all 8 deliberate mutants rejected' + ('; sockets PENDING host run' if pending else '; embedded relay PASS'))
    return 3 if pending else 0


if __name__ == '__main__':
    raise SystemExit(main())
