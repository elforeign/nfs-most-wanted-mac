"""Loopback peers use the production UDP relay; no game or router traffic.
Proof: N=0 exit 0/0 welcomed; N=6 exit 0/6 distinct welcomed, 30..40 states/peer
in two seconds. Missing HELLO: exit 1/0 welcomed/0 states; N=7 exit 2.
The default invocation still welcomes one peer without requiring a game pose.
"""
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time

RT = Path(__file__).resolve().parent
VERSION = int(re.search(r'#define ROAM_VERSION (\d+)', (RT/'roam_core.h').read_text())[1])


def fields(line):
    return dict(re.findall(r'(\w+)=([^ ]+)', line))


def exercise(exe, td, label, count, broken=False, public=None, private=None):
    with (td / (label + '.log')).open('w+') as log, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as host:
        host.bind(('127.0.0.1', 0))
        host.setblocking(False)
        proc = subprocess.Popen([str(exe), '2', '0', str(count)], stdout=log, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            ready = ''
            while time.monotonic() < deadline:
                with (td / (label + '.log')).open() as reader:
                    ready = reader.readline()
                if ready or proc.poll() is not None:
                    break
                time.sleep(.005)
            if not ready:
                _, err = proc.communicate(timeout=3)
                if proc.returncode == 3 and ('Operation not permitted' in err or 'Permission denied' in err):
                    return 3
                raise AssertionError((label, proc.returncode, err))
            match = re.search(r'ready port (\d+)', ready)
            assert match, ready
            relay = ('127.0.0.1', int(match[1]))
            cookie = b''
            host_id = seq = 0
            next_send = start = time.monotonic()
            poses, names, looks, phase_seen = {}, {}, {}, set()
            host_bytes = host_packets = 0
            while proc.poll() is None and time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_send:
                    next_send = now + .05
                    def send(kind, body):
                        nonlocal seq
                        host.sendto(struct.pack('<2sBBHH', b'RM', VERSION, kind, host_id, seq) + body, relay)
                        seq = (seq + 1) & 65535
                    if not host_id:
                        base = struct.pack('<I16s', 1, b'FixtureHost') + public
                        proof = b''
                        if cookie:
                            signed = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(private)],
                                                    input=b'NFSMW relay installation proof\0'+bytes([VERSION])+base+cookie,
                                                    capture_output=True, timeout=10)
                            assert signed.returncode == 0
                            proof = bytes([len(signed.stdout)]) + signed.stdout.ljust(72, b'\0')
                        send(1, base + cookie + proof)
                    else:
                        second = now - start >= .8
                        pos = (200., 300., 40.) if second else (100., 200., 30.)
                        quat = (0, 0, 23170, 23170) if second else (0, 0, 0, 32767)
                        state = struct.pack('<I6f4hHH', int(now * 1000) & 0xffffffff,
                                            *pos, 1., 2., 0., *quat, 1, 7)
                        send(3, state)
                        send(5, struct.pack('<HBI2I', 7, 2, 0x12345678, 23, 42))
                while True:
                    try:
                        data, sender = host.recvfrom(1201)
                    except BlockingIOError:
                        break
                    host_bytes += len(data) + 28
                    host_packets += 1
                    assert sender == relay
                    magic, version, kind, peer_id, _ = struct.unpack_from('<2sBBHH', data)
                    assert (magic, version) == (b'RM', VERSION)
                    if kind == 11:
                        assert len(data) == 28
                        cookie = data[8:]
                    elif kind == 2:
                        host_id = struct.unpack_from('<H', data, 8)[0]
                    elif kind == 4:
                        assert len(data) == 9 + data[8] * 44
                        for i in range(data[8]):
                            off = 9 + i * 44
                            remote = struct.unpack_from('<H', data, off)[0]
                            state = struct.unpack_from('<I6f4hHH', data, off + 4)
                            poses[remote] = state
                            phase_seen.add(state[3] > 35)
                    elif kind == 5:
                        looks[peer_id] = data[8:]
                    elif kind == 10:
                        names[peer_id] = data[8:].rstrip(b'\0').decode('ascii')
                time.sleep(.002)
            _, err = proc.communicate(timeout=3)
            assert proc.returncode == (1 if broken else 0), (label, err[-2000:])
            log.seek(0)
            lines = log.read().splitlines()
            peers = [fields(s) for s in lines if s.startswith('peer=')]
            ticks = [fields(s) for s in lines if s.startswith('relay_tick ')]
            summary = fields(lines[-1])
            welcomed = sum(int(p['welcomed']) for p in peers)
            assert welcomed == (0 if broken else count), peers
            assert len(peers) == count
            assert int(summary['welcomed']) == welcomed
            assert int(summary['game_states']) >= 30
            assert int(summary['ports_unique']) == 1
            assert 30 <= len(ticks) <= 40, len(ticks)
            assert all(int(t['wall_ns']) >= 0 for t in ticks)
            assert all(int(t['cpu_ns']) >= 0 for t in ticks)
            assert all(int(b['mono_ns']) > int(a['mono_ns']) for a, b in zip(ticks, ticks[1:]))
            assert sum(int(t['d_ticks']) for t in ticks) == len(ticks)
            for key in ['d_rx_bad', 'd_rx_rate_dropped', 'd_tx_errors', 'd_full']:
                assert sum(int(t[key]) for t in ticks) == 0, (label, key)
            assert int(ticks[-1]['clients']) == 1 + welcomed
            actual_bytes = host_bytes + sum(int(p['rx_wire_bytes']) for p in peers)
            actual_packets = host_packets + sum(int(p['rx_packets']) for p in peers)
            assert actual_bytes <= 256*1024 + 4*1024*1024*2, actual_bytes
            assert actual_packets <= 512 + 8192*2, actual_packets
            assert sum(int(t['d_tx_wire_bytes']) for t in ticks) <= 256*1024 + 4*1024*1024*2
            if not broken and count:
                ids = {int(p['id']) for p in peers}
                assert len(ids) == count and 0 not in ids and host_id not in ids
                assert ids == set(poses) == set(names) == set(looks)
                assert phase_seen == {False, True}, phase_seen
                for i, p in enumerate(peers):
                    assert 30 <= int(p['state_received']) == int(p['state_sent']) <= 40, p
                    assert p['look_sent'] == p['state_sent'], p
                    assert int(p['bundles']) >= 30 and int(p['entries']) >= 30, p
                    peer_id = int(p['id'])
                    assert names[peer_id] == ('Roadrunner' if i == 0 else f'Roadrunner{i+1}')
                    assert looks[peer_id] == struct.pack('<HBI2I', 7, 2, 0x12345678, 23, 42)
                    state = poses[peer_id]
                    ahead, side = 12 + 6 * (i // 2), (-1 if i & 1 else 1) * (4 + 2 * (i // 2))
                    assert abs(state[1] - (200 + side)) < .01, state
                    assert abs(state[2] - (300 - ahead)) < .01, state
                    assert abs(state[3] - 40) < .01, state
                    assert state[4:7] == (1., 2., 0.) and state[-2:] == (3, 7), state
            else:
                assert not poses
                assert all(int(p['state_received']) == int(p['state_sent']) == 0 for p in peers)
            print(f'{label}: exit={proc.returncode} welcomed={welcomed}/{count} '
                  f'states={[int(p["state_received"]) for p in peers]} ticks={len(ticks)}', flush=True)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    return 0


def main():
    source = (RT / 'roam_loopback_fixture.c').read_text()
    hello = 'if (!p->client.welcomed || (!has && (uint32_t)(now-p->hello_ms)>=1000))'
    assert source.count(hello) == 1
    with tempfile.TemporaryDirectory(prefix='roam-visual-') as tmp:
        td = Path(tmp)
        private = td/'host-key.pem'
        generated = subprocess.run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', str(private)], capture_output=True, timeout=10)
        assert generated.returncode == 0
        private.chmod(0o600)
        exported = subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-outform', 'DER'], capture_output=True, timeout=10)
        assert exported.returncode == 0 and exported.stdout[-65] == 4
        public = exported.stdout[-65:]
        for label, text in [('normal', source), ('broken', source.replace(hello, 'if (0)'))]:
            src = td / (label + '.c'); src.write_text(text)
            exe = td / label
            p = subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-g', '-pthread',
                                '-fsanitize=address,undefined', '-I', str(RT), str(src),
                                str(RT/'roam_auth.c'), str(RT/'roam_moderation.c'), str(RT/'roam_core.c'), str(RT/'roam_relay.c'), '-o', str(exe)] + (['-framework', 'Security', '-framework', 'CoreFoundation'] if sys.platform == 'darwin' else ['-lcrypto', '-lm']),
                               capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-2000:]
            for count in ([0, 6] if label == 'normal' else [6]):
                if exercise(exe, td, f'{label}-{count}', count, label == 'broken', public, private) == 3:
                    return 3
            if label == 'normal':
                p = subprocess.run([str(exe), '1', '0', '7'], capture_output=True, text=True, timeout=5)
                assert p.returncode == 2 and not p.stdout, (p.returncode, p.stderr)
                print('N=7: exit=2 (refused before binding)', flush=True)
                p = subprocess.run([str(exe), '1', '0'], capture_output=True, text=True, timeout=5)
                assert p.returncode == 0 and 'peers=1 welcomed=1 game_states=0' in p.stdout, p.stderr
                print('default: exit=0 welcomed=1/1 (no host)', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
