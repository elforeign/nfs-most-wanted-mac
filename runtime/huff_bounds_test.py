"""Native HUFF bounds proof, with no emulator or assets.

Good: 2/2 native decodes (standalone sanitizer and Setup shared-header bridge).
Broken: 1/1 134-byte symbol-count-wrap stream rejected by each entry point;
removing the pre-addition check makes the sanitizer control fail (1/1).
The review stream has lengths 1..8 empty, then 511/4294965507/1534 symbols;
the old uint32 total wraps to 256 and the lookup builder reads sym[256].
"""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import huff


def wrap_fixture():
    def num(value):
        if value < 4:
            return format(value + 4, '03b')
        for k in range(30):
            n = k + 2 + (k >= 16)
            if n <= 31 and 0 <= value - (1 << n) + 4 < (1 << n):
                return '0' * k + '1' + format(value - (1 << n) + 4, f'0{n}b')
        raise ValueError(value)
    bits = f'{0x30fb:016b}{1:024b}{255:08b}'
    bits += ''.join(num(c) for c in [0] * 8 + [511, 4294965507, 1534])
    bits += num(0) * 256
    bits += '0' * (-len(bits) % 16)
    return b'HUFF\x01\x10\0\0' + struct.pack('<II', 1, len(bits) // 8) + int(bits, 2).to_bytes(len(bits) // 8, 'big')


class HuffBounds(unittest.TestCase):
    def test_native_and_setup(self):
        bad = wrap_fixture()
        self.assertEqual(len(bad), 134)
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            decoder = huff.NativeHuff(work)
            try:
                self.assertEqual(decoder(huff.fixture(b'proof')), b'proof')
                with self.assertRaisesRegex(ValueError, 'decoded 0/1'):
                    decoder(bad)
            finally:
                decoder.close()
            good = huff.fixture(b'proof')
            arrays = ''.join('static const unsigned char ' + name + '[]={' + ','.join(map(str, data)) + '};\n'
                             for name, data in (('good', good), ('bad', bad)))
            source = work / 'bounds.c'
            source.write_text('#include "huff.h"\n' + arrays +
                'int main(void) { unsigned char out[5]; '
                'if(huff_native(good,sizeof good,out,5)!=5 || memcmp(out,"proof",5)) return 2; '
                'return huff_native(bad,sizeof bad,out,5)!=0; }\n')
            header = (ROOT / 'runtime/huff.h').read_text()
            guard = '        if (c > 256 - nsym) return 0;\n'
            self.assertIn(guard, header)
            env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:abort_on_error=0',
                       UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
            for broken in (False, True):
                (work / 'huff.h').write_text(header.replace(guard, '') if broken else header)
                binary = work / ('broken' if broken else 'good')
                build = subprocess.run(['clang', '-O1', '-g', '-fsanitize=address,undefined',
                                        str(source), '-o', str(binary)], capture_output=True, timeout=120)
                self.assertEqual(build.returncode, 0, build.stderr.decode()[-2000:])
                run = subprocess.run([str(binary)], capture_output=True, timeout=20, env=env)
                if broken:
                    self.assertNotEqual(run.returncode, 0)
                    self.assertRegex(run.stderr.decode(), 'out of bounds|buffer-overflow')
                else:
                    self.assertEqual(run.returncode, 0, run.stderr.decode()[-2000:])
        print('good: 2/2 native/Setup HUFF paths; broken: wrap rejected in both, 1/1 old-code sanitizer control failed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
