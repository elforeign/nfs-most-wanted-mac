"""Installation proof, local moderation, restart and provider interoperability.
Normal counts and named broken-control results are recorded in relay-moderation-privacy.md.
Signature bypass, ignored bans, lost persistence and replay bypass must fail production-path assertions.
Loopback-only UDP and permission-protected Unix control; no game/router traffic.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import selectors
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
      '-g', '-fsanitize=address,undefined', '-I', str(RT)]
LIBS = ['-framework', 'Security', '-framework', 'CoreFoundation'] if sys.platform == 'darwin' else ['-lcrypto', '-lm']
VERSION = int(re.search(r'#define ROAM_VERSION (\d+)', (RT/'roam_core.h').read_text())[1])
DOMAIN = b'NFSMW relay installation proof\0'


def temporary_directory(prefix):
    # Leave space for the longest fixture suffix and Darwin's 104-byte sun_path.
    candidates = dict.fromkeys((tempfile.gettempdir(), '/tmp', '/var/tmp'))
    for directory in candidates:
        if len(os.fsencode(str(Path(directory)/prefix))) + 8 + 52 >= 104:
            continue
        try:
            return tempfile.TemporaryDirectory(prefix=prefix, dir=directory)
        except OSError:
            pass
    raise RuntimeError('no writable temporary directory with a short Unix-socket path')


def execute(args, **kw):
    p = subprocess.run(list(map(str, args)), capture_output=True, timeout=60, **kw)
    assert p.returncode == 0, (args[0], p.returncode, p.stderr[-2000:])
    return p.stdout


def build(td, name, changes=None, main='roam_moderation_test.c'):
    sources = ['roam_auth.c', 'roam_moderation.c', 'roam_relay.c']
    paths = []
    for file in sources:
        source = (RT/file).read_text()
        if changes and changes[0] == file:
            old, new = changes[1:]
            assert source.count(old) == 1, (name, 'control anchor', old)
            source = source.replace(old, new)
        path = td/(name+'_'+file)
        path.write_text(source)
        paths.append(path)
    exe = td/name
    execute(CF + [RT/main, RT/'roam_core.c', *paths, *LIBS, '-o', exe])
    return exe


def directories(root):
    root.mkdir(mode=0o700)
    paths = [root/name for name in ('state', 'admin', 'userdata')]
    for p in paths:
        p.mkdir(mode=0o700)
    return paths


def provider_fixture(exe, td):
    td.mkdir(mode=0o700)
    execute([exe, '--fixture', td])
    raw = (td/'key').read_bytes()
    assert len(raw) == 97 and raw[0] == 4
    # SEC1 P-256 private key and SubjectPublicKeyInfo, DER. All material is disposable.
    private = bytes.fromhex('30770201010420') + raw[65:] + bytes.fromhex('a00a06082a8648ce3d030107a144034200') + raw[:65]
    public = bytes.fromhex('3059301306072a8648ce3d020106082a8648ce3d030107034200') + raw[:65]
    (td/'private.der').write_bytes(private)
    (td/'public.der').write_bytes(public)
    (td/'private.der').chmod(0o600)
    execute(['openssl', 'ec', '-inform', 'DER', '-in', td/'private.der', '-out', td/'private.pem'])
    (td/'private.pem').chmod(0o600)
    execute(['openssl', 'pkey', '-pubin', '-inform', 'DER', '-in', td/'public.der', '-out', td/'public.pem'])
    request = (td/'request').read_bytes()
    (td/'message').write_bytes(DOMAIN+bytes([VERSION])+request)
    execute(['openssl', 'dgst', '-sha256', '-verify', td/'public.pem', '-signature', td/'native.sig', td/'message'])
    execute(['openssl', 'dgst', '-sha256', '-sign', td/'private.pem', '-out', td/'foreign.sig', td/'message'])
    execute([exe, '--verify', td])
    signature = bytearray((td/'foreign.sig').read_bytes())
    signature[-1] ^= 1
    (td/'foreign.sig').write_bytes(signature)
    p = subprocess.run([str(exe), '--verify', str(td)], capture_output=True, timeout=30)
    assert p.returncode == 1 and b'verify FAIL' in p.stdout, 'cross-provider tamper control'
    print('cross-provider: native -> OpenSSL CLI and OpenSSL CLI -> native PASS; altered signature rejected', flush=True)
    return raw, td/'private.pem'


class Peer:
    def __init__(self, port, raw, private):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.connect(('127.0.0.1', port))
        self.sock.settimeout(0.25)
        self.base = struct.pack('<I', 7)+b'NameSentinel'.ljust(16, b'\0')+raw[:65]
        self.private = private
        self.id = 0
        self.proof = None

    def header(self):
        return b'RM'+bytes([VERSION, 1])+struct.pack('<HH', self.id, 1)

    def join(self, expect=True):
        # Drain old membership packets before each independent admission attempt.
        self.sock.setblocking(False)
        try:
            while self.sock.recv(1200):
                pass
        except BlockingIOError:
            pass
        self.sock.settimeout(0.25)
        self.sock.send(self.header()+self.base)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            try:
                b = self.sock.recv(1200)
            except socket.timeout:
                if not expect:
                    return False
                continue
            if len(b) < 8:
                continue
            if b[3] == 11:
                cookie = b[8:]
                assert len(cookie) == 20
                signature = execute(['openssl', 'dgst', '-sha256', '-sign', self.private], input=DOMAIN+bytes([VERSION])+self.base+cookie)
                assert 0 < len(signature) <= 72
                self.proof = self.header()+self.base+cookie+bytes([len(signature)])+signature.ljust(72, b'\0')
                self.sock.send(self.proof)
            elif b[3] == 2:
                self.id = struct.unpack('<H', b[8:10])[0]
                return True
        assert not expect, 'loopback admission timeout'
        return False

    def close(self):
        self.sock.close()


def admin(path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(str(path/'admin.sock'))
        s.sendall(command)
        out = bytearray()
        while True:
            b = s.recv(4096)
            if not b:
                return bytes(out)
            out.extend(b)
            assert len(out) < 3072


def start(exe, paths, port=0):
    state, control, _ = paths
    p = subprocess.Popen([str(exe), '--port', str(port), '--state-dir', str(state), '--admin-dir', str(control)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout, selectors.EVENT_READ)
            assert sel.select(15), 'relay startup timeout'
        line = p.stdout.readline()
        m = re.search(rb'listening port (\d+)', line)
        assert m, ('relay startup', p.poll(), p.stderr.read(2000))
        return p, int(m[1]), line
    except BaseException:
        p.kill()
        p.wait()
        raise


def stop(p):
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, ('relay cleanup', p.returncode, err[-1000:])
    return out+err


def loopback(exe, td, raw, private):
    paths = directories(td)
    state, control, _ = paths
    output = bytearray()
    p, port, line = start(exe, paths)
    output.extend(line)
    peers = []
    try:
        peer = Peer(port, raw, private)
        peers.append(peer)
        assert peer.join()
        identifier = hashlib.sha256(raw[:65]).hexdigest().encode()
        listed = admin(control, b'list 0\n')
        assert execute([exe, '--control', control/'admin.sock', '--request', 'list 0']) == listed, 'shipped local control client'
        bad_command = subprocess.run([str(exe), '--control', str(control/'admin.sock'), '--request', 'ban 0 60'], capture_output=True, timeout=15)
        assert bad_command.returncode == 1 and bad_command.stdout == b'ERR syntax\n'
        assert identifier in listed and b'NameSentinel' not in listed, 'local identifiers only'
        assert (control/'admin.sock').stat().st_mode & 0o777 == 0o600, 'socket permissions'
        proof = peer.proof
        assert execute([exe, '--control', control/'admin.sock', '--request', f'kick {peer.id}']) == b'OK\n'
        assert admin(control, b'list 0\n') == b'END 0\n', 'kick removes live session'
        peer.sock.send(proof)
        time.sleep(0.05)
        assert admin(control, b'list 0\n') == b'END 0\n', 'replay after kick'
        assert peer.join(), 'kick permits a fresh authenticated reconnect'
        other = Peer(port, raw, private)
        peers.append(other)
        assert other.join()
        assert admin(control, f'ban {peer.id} 60\n'.encode()) == b'OK\n'
        assert admin(control, b'list 0\n') == b'END 0\n', 'ban removes all installation sessions'
        assert not peer.join(False), 'banned reconnect'
        state_bytes = (state/'bans').read_bytes()
        assert re.fullmatch(identifier+rb' [0-9]+\n', state_bytes), 'persistence contents'
        assert (state/'bans').stat().st_mode & 0o777 == 0o600
        output.extend(stop(p))
        p, again, line = start(exe, paths, port)
        output.extend(line)
        assert again == port and not peer.join(False), 'persistent restart ban'
        assert admin(control, b'unban '+identifier+b'\n') == b'OK\n'
        assert peer.join(), 'unban reconnect'
        assert admin(control, f'ban {peer.id} 1\n'.encode()) == b'OK\n'
        output.extend(stop(p))
        time.sleep(1.1)
        p, _, line = start(exe, paths, port)
        output.extend(line)
        assert peer.join(), 'restart-safe expiry'
        assert admin(control, b'list -1\n') == b'ERR syntax\n'
        assert admin(control, b'list 0\nkick 1\n') == b'ERR syntax\n'
        assert admin(control, b'X'*161+b'\n') == b'ERR syntax\n'
        assert admin(control, b'list 0\0junk\n') == b'ERR syntax\n'
        output.extend(stop(p))
        p = None
    finally:
        for peer in peers:
            peer.close()
        if p and p.poll() is None:
            p.kill()
            p.wait()
    (td/'service.log').write_bytes(output)
    (td/'export.txt').write_bytes(output)
    print('loopback: kick, replay, reconnect, all-session ban, restart, unban and restart expiry PASS; 4 malformed controls', flush=True)
    return output, state_bytes


def unsafe_storage(exe, td):
    paths = directories(td)
    state, control, user = paths
    sentinel_id = hashlib.sha256(b'storage-fixture').hexdigest()
    cases = [b'', b'garbage\n', (sentinel_id+' 18446744073709551616\n').encode(),
             (sentinel_id+' 0\n').encode(), (sentinel_id+' 100\n').encode()*2,
             b'x'*90000, b''.join((hashlib.sha256(str(i).encode()).hexdigest()+' 100\n').encode() for i in range(1025))]
    for data in cases:
        (state/'bans').write_bytes(data)
        (state/'bans').chmod(0o600)
        p = subprocess.run([str(exe), '--port', '0', '--state-dir', str(state), '--admin-dir', str(control)],
                           capture_output=True, timeout=15)
        assert p.returncode == 1 and b'moderation storage/control failed' in p.stderr and b'listening' not in p.stdout, 'corrupt state'
    (state/'bans').write_text('# empty\n')
    for target in (state, control, state/'bans'):
        safe_mode = target.stat().st_mode & 0o777
        target.chmod(0o777 if target.is_dir() else 0o644)
        p = subprocess.run([str(exe), '--port', '0', '--state-dir', str(state), '--admin-dir', str(control)], capture_output=True, timeout=15)
        target.chmod(safe_mode)
        assert p.returncode == 1 and b'listening' not in p.stdout, 'unsafe permissions'
    (state/'bans').unlink()
    (state/'bans').symlink_to(user/'missing')
    p = subprocess.run([str(exe), '--port', '0', '--state-dir', str(state), '--admin-dir', str(control)], capture_output=True, timeout=15)
    assert p.returncode == 1, 'ban symlink'
    (state/'bans').unlink()
    print('storage: 7 corrupt/excessive files, 3 unsafe modes and symlink rejected before UDP listener', flush=True)


def credential_storage(exe, td):
    td.mkdir(mode=0o700)
    profile = td/'scratch'
    profile.mkdir(mode=0o700)
    execute([exe, '--load', profile])
    leaf = profile/'online-identity'
    key = leaf/'key'
    original = key.read_bytes()
    assert len(original) == 97 and key.stat().st_mode & 0o777 == 0o600 and leaf.stat().st_mode & 0o777 == 0o700
    if sys.platform == 'darwin':
        assert execute(['xattr', '-p', 'com.apple.metadata:com_apple_backup_excludeItem', leaf])
        assert leaf.stat().st_flags & 1, 'UF_NODUMP'
    other = td/'other-scratch'
    other.mkdir(mode=0o700)
    execute([exe, '--load', other])
    assert (other/'online-identity/key').read_bytes() != original, 'scratch identities isolated'
    for data in (b'', original[:-1], original+b'x', bytes(97), original[:65]+bytes(32)):
        key.write_bytes(data)
        p = subprocess.run([str(exe), '--load', str(profile)], capture_output=True, timeout=15)
        assert p.returncode == 1 and key.read_bytes() == data, 'corrupt credential silently replaced'
    key.write_bytes(original)
    for target, mode in ((key, 0o644), (leaf, 0o755)):
        before = target.stat().st_mode & 0o777
        target.chmod(mode)
        p = subprocess.run([str(exe), '--load', str(profile)], capture_output=True, timeout=15)
        target.chmod(before)
        assert p.returncode == 1, 'unsafe credential permissions'
    key.unlink()
    key.symlink_to(other/'online-identity/key')
    p = subprocess.run([str(exe), '--load', str(profile)], capture_output=True, timeout=15)
    assert p.returncode == 1, 'credential symlink'
    key.unlink()
    os.link(other/'online-identity/key', key)
    p = subprocess.run([str(exe), '--load', str(profile)], capture_output=True, timeout=15)
    assert p.returncode == 1, 'credential hardlink'
    key.unlink()
    fresh = td/'concurrent'
    fresh.mkdir(mode=0o700)
    writers = [subprocess.Popen([str(exe), '--load', str(fresh)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for process in writers:
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, ('concurrent credential creation', out, err)
    assert sorted(p.name for p in (fresh/'online-identity').iterdir()) == ['key']
    print('credentials: isolated scratch profiles; reload; 5 corrupt values, 2 unsafe modes, symlink/hardlink refused; 2 concurrent writers PASS', flush=True)


def main():
    report = {'mutation_failures': {}}
    with temporary_directory('roam-mod-') as root:
        td = Path(root)
        normal = build(td, 'normal')
        paths = directories(td/'normal-state')
        p = subprocess.run([str(normal), *map(str, paths)], capture_output=True, timeout=60)
        print(p.stdout.decode().strip(), flush=True)
        assert p.returncode == 0, p.stderr.decode()[-2000:]
        report['normal_checks'] = int(re.search(r'MODERATION PASS: (\d+) checks', p.stdout.decode())[1])
        report['authentication_checks'] = int(re.search(r'authentication: checks=(\d+)', p.stdout.decode())[1])
        report['moderation_checks'] = int(re.search(r'moderation: checks=(\d+)', p.stdout.decode())[1])
        mutants = {
            'signature_bypass': ('roam_relay.c', 'if (!roam_auth_verify(', 'if (0 && !roam_auth_verify(', 'signature_required'),
            'ignored_bans': ('roam_moderation.c', 'if (!m) return 0;\n    for (int i = 0;', 'if (!m) return 0;\n    return 0;\n    for (int i = 0;', 'ban_existing_sessions'),
            'lost_persistence': ('roam_moderation.c', 'if (ok && renameat(', 'if (ok && 0 && renameat(', 'persistent_ban_restart'),
            'replay_bypass': ('roam_relay.c', 'else if (!memcmp(r->proofs[i].digest, proof, 32))', 'else if (0 && !memcmp(r->proofs[i].digest, proof, 32))', 'replayed_admission'),
        }
        for name, (file, old, new, assertion) in mutants.items():
            exe = build(td, name, (file, old, new))
            paths = directories(td/(name+'-state'))
            p = subprocess.run([str(exe), *map(str, paths)], capture_output=True, timeout=60)
            assert p.returncode == 1 and ('FAIL '+assertion).encode() in p.stdout, (name, p.returncode, p.stdout[-2500:], p.stderr[-1000:])
            report['mutation_failures'][name] = int(re.search(r'MODERATION FAIL: \d+ checks, (\d+) failures', p.stdout.decode())[1])
            print(name+': '+p.stdout.decode().splitlines()[-1]+'; named control rejected', flush=True)
        credential_storage(normal, td/'credential-storage')
        raw, private = provider_fixture(normal, td/'provider')
        cli = build(td, 'relay', main='roam_relay_main.c')
        loopback(cli, td/'loopback', raw, private)
        unsafe_storage(cli, td/'unsafe')
    report.update(native_provider='Security' if sys.platform == 'darwin' else 'OpenSSL', cli_provider=execute(['openssl', 'version']).decode().strip(), cross_provider=True, restart_ban=True, restart_expiry=True, unban=True)
    report_path = RT.parent/'.metal-cache/roam-moderation-proof.json'
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2)+'\n')
    print('ROAM MODERATION PASS: signatures, 4 named mutations, provider interoperability, persistence and loopback controls')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
