"""Relay security regressions: ACL storage, canonical points and public-id moderation.
Original/fixed: ACL 4/4 -> 4/0 failing cases; alias 10/3 -> 10/0 checks/failures;
public-id 3/3 -> 3/0 failing tests. Overall: 5 tests/8 failures -> 5 tests/0 failures.
No history reads; all sockets use loopback and disposable local state.
"""
from pathlib import Path
import hashlib
import importlib.util
import subprocess
import sys
import unittest

RT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('moderation_fixture', RT/'roam_moderation_test.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class Security(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = fixture.temporary_directory('relay-sec-')
        cls.td = Path(cls.scratch.name)
        cls.native = fixture.build(cls.td, 'native')
        cls.relay = fixture.build(cls.td, 'relay', main='roam_relay_main.c')
        cls.raw, cls.private = fixture.provider_fixture(cls.native, cls.td/'provider')
        cls.identifier = hashlib.sha256(cls.raw[:65]).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_canonical_alias_ban(self):
        state, control, _ = fixture.directories(self.td/'alias')
        p = subprocess.run([str(self.native), '--alias', str(state), str(control)],
                           capture_output=True, timeout=30)
        print(p.stdout.decode().strip(), flush=True)
        self.assertEqual(p.returncode, 0, p.stderr.decode()[-1000:])

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS extended ACLs')
    def test_credential_acls(self):
        profile = self.td/'acl'
        profile.mkdir(mode=0o700)
        fixture.execute([self.native, '--load', profile])
        leaf, key = profile/'online-identity', profile/'online-identity/key'
        original = key.read_bytes()
        # POSIX modes stay private while ACLs grant another principal access.
        for target, grant in ((key, 'everyone allow read'), (leaf, 'everyone allow list,search')):
            with self.subTest(existing=target.name):
                fixture.execute(['chmod', '+a', grant, target])
                try:
                    self.assertEqual(target.stat().st_mode & 0o077, 0)
                    p = subprocess.run([str(self.native), '--load', str(profile)], capture_output=True, timeout=15)
                    self.assertEqual(p.returncode, 1, 'existing ACL grant accepted')
                    self.assertEqual(key.read_bytes(), original, 'unsafe credential replaced')
                finally:
                    fixture.execute(['chmod', '-N', target])
        inherited = self.td/'inherited'
        inherited.mkdir(mode=0o700)
        fixture.execute(['chmod', '+a', 'everyone allow read,readattr,readextattr,readsecurity,list,search,file_inherit,directory_inherit', inherited])
        try:
            fixture.execute([self.native, '--load', inherited])
            for target in (inherited/'online-identity', inherited/'online-identity/key'):
                with self.subTest(created=target.name):
                    listing = fixture.execute(['ls', '-lde', target])
                    self.assertNotIn(b'everyone', listing, 'inherited ACL retained on credentials')
            fixture.execute([self.native, '--load', inherited])
        finally:
            fixture.execute(['chmod', '-RN', inherited])

    def public_id_control(self, operation):
        paths = fixture.directories(self.td/operation)
        _, control, _ = paths
        p, port, _ = fixture.start(self.relay, paths)
        peers = [fixture.Peer(port, self.raw, self.private) for _ in range(2)]
        try:
            for peer in peers:
                self.assertTrue(peer.join())
            request = f'{operation} {self.identifier}'+(' 60' if operation == 'ban' else '')+'\n'
            self.assertEqual(fixture.admin(control, request.encode()), b'OK\n')
            self.assertEqual(fixture.admin(control, b'list 0\n'), b'END 0\n', 'all matching sessions removed')
            self.assertEqual(peers[0].join(operation == 'kick'), operation == 'kick')
            if operation == 'ban':
                fixture.stop(p)
                p, _, _ = fixture.start(self.relay, paths, port)
                self.assertFalse(peers[0].join(False), 'identifier ban survives restart')
            else:
                self.assertEqual(fixture.admin(control, request.encode()), b'OK\n')
                self.assertEqual(fixture.admin(control, request.encode()), b'ERR session\n')
        finally:
            for peer in peers:
                peer.close()
            if p.poll() is None:
                fixture.stop(p)

    def test_public_identifier_kick(self):
        self.public_id_control('kick')

    def test_public_identifier_ban(self):
        self.public_id_control('ban')

    def test_disconnected_identifier_ban(self):
        paths = fixture.directories(self.td/'disconnected')
        state, control, _ = paths
        p, port, _ = fixture.start(self.relay, paths)
        peer = fixture.Peer(port, self.raw, self.private)
        try:
            self.assertEqual(fixture.admin(control, f'ban {self.identifier} 60\n'.encode()), b'OK\n')
            self.assertTrue((state/'bans').read_bytes().startswith(self.identifier.encode()+b' '))
            self.assertFalse(peer.join(False))
            self.assertEqual(fixture.admin(control, f'unban {self.identifier}\n'.encode()), b'OK\n')
            self.assertTrue(peer.join())
            for target in (self.identifier.upper(), 'g'*64, self.identifier+'0'):
                self.assertEqual(fixture.admin(control, f'ban {target} 60\n'.encode()), b'ERR syntax\n')
        finally:
            peer.close()
            if p.poll() is None:
                fixture.stop(p)


if __name__ == '__main__':
    unittest.main(verbosity=2)
