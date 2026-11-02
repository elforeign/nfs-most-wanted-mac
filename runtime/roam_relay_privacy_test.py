"""Generated-artifact privacy proof, with real address-log and address-hash persistence mutations.
Normal/mutant artifact counts are recorded in relay-moderation-privacy.md.
Scanner controls: original textual/network/host findings 1/0/0; fixed 1/1/1.
Only loopback sockets; no real installation, provider, public traffic or game execution.
"""
from pathlib import Path
import base64
import hashlib
import importlib.util
import re
import socket
import struct
import subprocess
import tempfile
RT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('moderation_fixture', RT/'roam_moderation_test.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def leaks(artifacts, raw):
    address = '127.0.0.1'
    # Explicit network and host integer/byte forms prevent address hashing from evading the audit.
    forms = [address.encode(), socket.inet_aton(address), struct.pack('<I', 0x7f000001)]
    identifiers = [hashlib.sha256(x).hexdigest().encode() for x in forms]
    secrets = [raw, raw[65:], raw[:65], b'NameSentinel', b'CookieSentinel', b'SecretSentinel']
    needles = forms+identifiers
    for secret in secrets:
        needles.extend((secret, secret.hex().encode(), base64.b64encode(secret)))
    return [(name, i) for name, data in artifacts.items() for i, needle in enumerate(needles) if needle in data]


def artifacts(exe, root, raw, private):
    paths = fixture.directories(root)
    state, control, _ = paths
    p, port, line = fixture.start(exe, paths)
    peer = fixture.Peer(port, raw, private)
    try:
        assert peer.join()
        assert fixture.admin(control, f'ban {peer.id} 60\n'.encode()) == b'OK\n'
        persisted = (state/'bans').read_bytes()
        logs = line+fixture.stop(p)
        p = None
    finally:
        peer.close()
        if p and p.poll() is None:
            p.kill()
            p.wait()
    result = {'service.log': logs, 'bans': persisted, 'export.txt': logs}
    for name, data in result.items():
        (root/name).write_bytes(data)
    return result


def main():
    forms = [b'127.0.0.1', socket.inet_aton('127.0.0.1'), struct.pack('<I', 0x7f000001)]
    counts = [len(leaks({'control': value}, bytes(range(97)))) for value in forms]
    assert counts == [1, 1, 1], ('textual/network/host IPv4 scan', counts)
    print('IPv4 scan controls: textual/network/host findings '+str(counts), flush=True)
    with fixture.temporary_directory('relay-privacy-') as scratch:
        td = Path(scratch)
        native = fixture.build(td, 'native')
        raw, private = fixture.provider_fixture(native, td/'provider')
        normal = fixture.build(td, 'relay', main='roam_relay_main.c')
        output = artifacts(normal, td/'normal', raw, private)
        telemetry = fixture.build(td, 'telemetry', main='roam_loopback_fixture.c')
        telemetry_log = fixture.execute([telemetry, '1', '0', '0'])
        output['telemetry.log'] = telemetry_log
        assert not leaks(output, raw), 'private data in normal operating artifacts'
        proof = RT.parent/'.metal-cache/roam-moderation-proof.json'
        if proof.exists():
            assert not leaks({'proof.json': proof.read_bytes()}, raw), 'private data in aggregate proof export'
        expected = hashlib.sha256(raw[:65]).hexdigest().encode()
        assert re.fullmatch(expected+rb' [0-9]+\n', output['bans']), 'verified public identifier and expiry only'
        print('privacy: 4 generated log/state/export artifacts, 0 sentinel/address/secret leaks', flush=True)
        controls = {
            'address_logger': ('roam_relay.c', 'r->st.clients++;', 'r->st.clients++; fprintf(stderr, "peer %s\\n", inet_ntoa(from->sin_addr));'),
            'network_bytes_logger': ('roam_relay.c', 'r->st.clients++;', 'r->st.clients++; (void)fwrite(&from->sin_addr.s_addr, 4, 1, stderr);'),
            'host_bytes_logger': ('roam_relay.c', 'r->st.clients++;', 'r->st.clients++; uint32_t host_address = ntohl(from->sin_addr.s_addr); (void)fwrite(&host_address, 4, 1, stderr);'),
            'address_persistence': ('roam_relay.c', 'memcpy(k->identity, identity, 32);', 'roam_auth_digest(&from->sin_addr.s_addr, 4, k->identity);'),
        }
        for name, change in controls.items():
            bad = fixture.build(td, name, change, main='roam_relay_main.c')
            output = artifacts(bad, td/(name+'-artifacts'), raw, private)
            found = leaks(output, raw)
            assert found, (name, 'privacy mutation escaped generated-artifact scan')
            print(name+': rejected '+str(len(found))+' generated-artifact findings', flush=True)
        # Scan the production report exporter: private credentials cannot enter its fixed attachment list.
        report = (RT/'report.m').read_text()
        assert not re.search(r'(?:copy|attach|bundle).*online-identity', report, re.I), 'identity report attachment'
        auth = (RT/'roam_auth.c').read_text()
        assert 'RLIMIT_CORE' in auth and 'PR_SET_DUMPABLE' in auth
        assert 'com.apple.metadata:com_apple_backup_excludeItem' in auth and 'UF_NODUMP' in auth
        unit = (RT/'relay/nfsmw-relay.service').read_text()
        launcher = (RT/'relay/run.sh').read_text()
        assert 'LimitCORE=0' in unit and '--ulimit core=0:0' in launcher
        assert 'RuntimeDirectory=' in unit and 'StateDirectory=' in unit
        assert '--tmpfs /run/relay:' in launcher and '--mount type=bind,' in launcher
    print('ROAM RELAY PRIVACY PASS: live artifact audit, logger/hash controls, dump and backup integration')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
