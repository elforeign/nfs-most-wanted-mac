#!/usr/bin/env python3
"""Offline production C vs convert_mpf.py; all generated files stay in temporary scratch.
Run: $TOOLS_PY -I runtime/x360music_test.py
No game launch, network, or writes to the extract/game. Requires clang and retail assets.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(os.environ.get('NFS_PARENT') or Path(__file__).resolve().parent.parent.parent)
X = ASSETS / 'x360/files/SOUND/PFDATA/MW_MUSIC.MPF'
MUS = X.with_suffix('.MUS')
PC_GAME = ASSETS / 'Need For Speed Most Wanted Black Edition'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    spec = importlib.util.spec_from_file_location('reference', ROOT / 'tools_snapshot/x360_mpf_ref.py')
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    base_env = dict(os.environ, NFS_X360_MUSIC='on', NFS_X360_AUDIO='off')
    base_env.pop('NFS_MUSIC_TEST_PREF', None)
    with tempfile.TemporaryDirectory(prefix='.nfsmw_music_', dir=ROOT) as directory:
        scratch = Path(directory)
        binary = scratch / 'x360music_test'
        run('clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-O1',
            '-fsanitize=address,undefined', '-I', str(ROOT / 'recomp'),
            str(ROOT / 'runtime/x360music_test.c'), str(ROOT / 'runtime/settings.c'), str(ROOT / 'runtime/offline_stubs.c'), '-o', str(binary))
        run(str(binary), 'settings', env=base_env)
        converted = scratch / 'c.mpf'
        run(str(binary), 'convert', str(X), str(converted), env=base_env)
        ref.X = X
        ref.PC = PC_GAME / 'SOUND/PFDATA/MW_Music.mpf'
        ref.OUT = scratch / 'python.mpf'
        previous = Path.cwd()
        try:
            os.chdir(scratch)
            ref.main()
        finally:
            os.chdir(previous)
        c_bytes, py_bytes = converted.read_bytes(), ref.OUT.read_bytes()
        c_hash, py_hash = (hashlib.sha256(b).hexdigest() for b in (c_bytes, py_bytes))
        print(f'C SHA-256:      {c_hash}', flush=True)
        print(f'Python SHA-256: {py_hash}', flush=True)
        assert c_bytes == py_bytes, 'C output differs from the Python reference'
        print(f'byte-identical: {len(c_bytes)} bytes, 3257 segments', flush=True)

        def serve(extract, want, reason=None, **env):
            result = run(str(binary), 'serve', str(extract), str(PC_GAME), str(converted),
                         str(int(want)), env=dict(base_env, **env), text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            print(result.stdout, end='', flush=True)
            if reason:
                assert 'PC pair kept; conversion/validation failed:' in result.stdout
                assert reason in result.stdout, result.stdout

        serve(X.parents[2], True)
        serve(X.parents[2], False, NFS_X360_MUSIC='off', NFS_X360_AUDIO='on')
        # A genuinely absent setting uses the same default as settings_init.
        default_env = dict(base_env)
        default_env.pop('NFS_X360_MUSIC')
        run(str(binary), 'serve', str(X.parents[2]), str(PC_GAME), str(converted),
            '1', env=default_env)
        serve(X.parents[2], True, NFS_X360_MUSIC='', NFS_MUSIC_TEST_PREF='on')
        fixture = scratch / 'extract' / 'sOuNd' / 'pFdAtA'
        fixture.mkdir(parents=True)
        mpf, mus = fixture / 'mW_mUsIc.MpF', fixture / 'mW_mUsIc.MuS'
        original = X.read_bytes()
        mpf.write_bytes(original)
        serve(scratch / 'extract', False)  # MUS missing
        mpf.unlink()
        mus.symlink_to(MUS)
        serve(scratch / 'extract', False)  # MPF missing
        mpf.write_bytes(original)
        serve(scratch / 'extract', True)  # mixed-case extract filenames
        for label, offset, value in (
            ('graph', 8, bytes([original[8] ^ 1])),
            ('duration', 0x1ae10, struct.pack('>I', 123)),
            ('offset', 0x1ae0c, struct.pack('>I', 0xffffffff)),
            ('magic', 0, b'BAD!'),
        ):
            damaged = bytearray(original)
            damaged[offset:offset + len(value)] = value
            mpf.write_bytes(damaged)
            print(f'negative fixture: {label}', flush=True)
            serve(scratch / 'extract', False)
        mpf.write_bytes(original[:-1])
        serve(scratch / 'extract', False)
        mpf.write_bytes(original)
        mus.unlink()
        # Copy before truncating: never modify the symlink's retail target.
        shutil.copyfile(MUS, mus)
        last = struct.unpack_from('<I', c_bytes, 0x1ae0c + 3256 * 8)[0] << 7
        with mus.open('r+b') as stream:
            stream.seek(last + 4)
            header_size = struct.unpack('<I', stream.read(4))[0]
            terminal = last
            while True:
                stream.seek(terminal)
                tag, size = struct.unpack('<4sI', stream.read(8))
                assert size >= 8 and terminal + size <= MUS.stat().st_size
                if tag == b'SCEl':
                    break
                terminal += size
        first_data = 256 + 40 + 12  # first SCHl + SCCl
        next_segment = struct.unpack_from('<I', c_bytes, 0x1ae0c + 8)[0] << 7
        for label, pos, replacement in (
            ('zero chunk length', first_data + 4, struct.pack('<I', 0)),
            ('short chunk length', first_data + 4, struct.pack('<I', 7)),
            ('oversized chunk', first_data + 4, struct.pack('<I', 0xffffffff)),
            ('chunk overlaps next segment', first_data + 4, struct.pack('<I', next_segment - first_data + 1)),
            ('missing terminal SCEl', terminal, b'SCDl'),
        ):
            with mus.open('r+b') as stream:
                stream.seek(pos)
                saved = stream.read(len(replacement))
                stream.seek(pos)
                stream.write(replacement)
            print(f'negative fixture: {label}', flush=True)
            serve(scratch / 'extract', False, reason='chunk extent outside MUS/segment')
            with mus.open('r+b') as stream:
                stream.seek(pos)
                stream.write(saved)
        for label, length, reason in (
            ('partial last SCEl', terminal + 7, 'segment 3256 missing SCEl/truncated chunk header'),
            ('partial last SCDl payload', terminal - 1, 'segment 3256 chunk extent outside MUS/segment'),
            ('ends after last SCHl', last + header_size, 'segment 3256 missing SCEl/truncated chunk header'),
        ):
            with mus.open('r+b') as stream:
                stream.truncate(length)
            print(f'negative fixture: MUS {label} ({length} bytes)', flush=True)
            serve(scratch / 'extract', False, reason=reason)
        mus.unlink()
        mus.write_bytes(bytes(512))
        serve(scratch / 'extract', False)  # invalid/truncated segment source
        print('PASS: production conversion, serving and deliberate fallback negatives', flush=True)


if __name__ == '__main__':
    main()
