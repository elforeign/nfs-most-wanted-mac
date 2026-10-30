"""Router I/O fixtures: no sockets/DNS. Normal lease/lost/owned/reassigned/hostname:
13/50/23/55/44 checks, zero failures. Broken controls: 2/8/4/4/5 failures.
Failed, conflicting, cancelled and lost Adds never delete. Confirmed Adds delete once,
including after a lost renewal, but changed clients/ports/descriptions or a failed ownership
query prevent deletion. Also covers finite leases, hostname URLs and pinned transport.
ASan/UBSan enabled.
"""
from pathlib import Path
import subprocess
import tempfile
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
      '-g', '-fsanitize=address,undefined', '-I', str(RT)]


def main():
    # The original implementation is kept as narrowly generated behavioral regressions.
    source = (RT / 'natmap.c').read_text()
    mutants = {
        'lease': ('        return -1;\n    }\n    char expected[80];', '''        if (!remove && strstr(reply, "<errorCode>725</errorCode>")) {
            char *lease = strstr(args, "<NewLeaseDuration>3600</NewLeaseDuration>");
            if (lease) strcpy(lease, "<NewLeaseDuration>0</NewLeaseDuration>");
            http(m->control, m->gateway, m->service, action, args, reply, sizeof reply, stop);
            return 0;
        }
        return -1;
    }
    char expected[80];'''),
        'lost': ('    if (http(m->control, m->gateway, m->service, action, args, reply, sizeof reply, stop)) {',
                 '    if (!remove) m->upnp_owned = 1;\n    if (http(m->control, m->gateway, m->service, action, args, reply, sizeof reply, stop)) {'),
        'owned': ('m->upnp_owned = !remove;', 'm->upnp_owned = 0;'),
        'reassigned': ('    if (remove) {\n        // A lease may have expired', '    if (0 && remove) {\n        // A lease may have expired'),
        'hostname': ('if (getaddrinfo(host, NULL, &hints, &all)) return -1;', 'if (1) return -1;\n        if (getaddrinfo(host, NULL, &hints, &all)) return -1;'),
    }
    with tempfile.TemporaryDirectory(prefix='natmap-') as tmp:
        td = Path(tmp)
        for label, mutation in [('normal', None), *mutants.items()]:
            src = td / (label + '.c')
            if mutation:
                old, new = mutation
                assert source.count(old) == 1, old
                src.write_text(source.replace(old, new))
            else:
                src.write_text(source)
            exe = td / label
            p = subprocess.run(CF + ['-DNATMAP_SOURCE="' + str(src) + '"', str(RT / 'natmap_test.c'),
                                    str(RT / 'natpmp.c'), str(RT / 'pcp.c'), str(RT / 'upnp.c'), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-3000:]
            for case in mutants if label == 'normal' else [label]:
                p = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=20)
                print(label + ': ' + p.stdout.strip(), flush=True)
                assert p.returncode == (0 if label == 'normal' else 1), p.stderr[-3000:]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
