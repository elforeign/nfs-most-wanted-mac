"""Menu session lifecycle with fake router/server backends; no real sockets/router traffic.
Proof: normal 125 checks/0 failures; broken host/protocol/upstream hint/wording/renewal: 1/3/6/7/8 failures.
Code/address log controls: 28 leaked codes / 3 leaked addresses rejected; normal zero.
Each starts/stops 20/20, deletes 16.
Includes RFC1918/shared-address boundaries with manual public IPv4, failed mapping, unavailable public IP,
NAT-PMP/PCP/UPnP, loopback unavailable, renewal failure, and timeout advice; no socket/router calls.
"""
from pathlib import Path
import subprocess
import re
import tempfile
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g', '-pthread',
      '-fsanitize=address,undefined', '-I', str(RT)]


def main():
    source = (RT / 'roam_online.c').read_text()
    old = 'set_target(1, 0x7f000001u, mapping.internal_port,'
    assert source.count(old) == 1
    with tempfile.TemporaryDirectory(prefix='roam-session-') as tmp:
        td = Path(tmp)
        for label, text in [('normal', source), ('broken', source.replace(old, 'set_target(1, external, mapping.internal_port,')),
                            ('broken_status', source.replace('mapping.protocol == 1 ? "NAT-PMP"', 'mapping.protocol == 1 ? "wrong"')),
                            ('broken_classification', source.replace('upstream_class(mapping.external_ip)', 'upstream_class(external)')),
                            ('broken_log', source.replace('mapping.mapped, renewed, mapping.protocol, upstream, hairpin);', 'mapping.mapped, renewed, mapping.protocol, upstream, hairpin);\n    fprintf(stderr, \"roam: host code %s\\n\", code);')),
                            ('broken_address_log', source.replace('mapping.mapped, renewed, mapping.protocol, upstream, hairpin);', 'mapping.mapped, renewed, mapping.protocol, upstream, hairpin);\n    fprintf(stderr, \"roam: %s\\n\", status);')),
                            ('broken_hint', source.replace('Possible double NAT / carrier NAT', 'Router blocks it')),
                            ('broken_renewal', source.replace('return half < 1000 ? 1000 : half;', '(void)half; return (uint64_t)(lifetime > 1 ? lifetime / 2 : 30) * 1000;'))]:
            src = td / (label + '.c'); src.write_text(text + '\nvoid test_publish_upstream(uint32_t ip) { mapping.external_ip=ip; publish_host(1); }\nuint64_t test_renewal_ms(uint32_t lifetime) { return renewal_ms(lifetime); }\n')
            exe = td / label
            p = subprocess.run(CF + [str(RT / 'roam_session_test.c'), str(src), str(RT / 'roam_code.c'), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-2500:]
            p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15)
            print(label + ':', p.stdout.strip(), flush=True)
            leaks = re.findall(r'(?<![\w-])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{5}(?![\w-])', p.stderr, re.I)
            addresses = re.findall(r'(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?!\w|\.[0-9])', p.stderr)
            if label == 'broken_log':
                assert leaks, 'code-bearing logger escaped detection'
                print('broken_log: rejected %d leaked codes' % len(leaks), flush=True)
            elif label == 'broken_address_log':
                assert addresses, 'address-bearing logger escaped detection'
                print('broken_address_log: rejected %d leaked addresses' % len(addresses), flush=True)
            else:
                assert not leaks and not addresses, 'endpoint leaked in session logs'
                assert p.returncode == (0 if label == 'normal' else 1), p.stderr[-2500:]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
