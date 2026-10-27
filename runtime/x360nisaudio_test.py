#!/usr/bin/env python3
"""Offline A10 NIS audio proof (see x360nisaudio_test.c). Builds the PC XA decoder closure from recomp/out, ASan+UBSan.
Usage: x360nisaudio_test.py [broken]   (broken: EVT per-event words left unswapped; expected FAIL)"""
from pathlib import Path
import re, subprocess, sys, tempfile
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT.parent
PC = ASSETS / 'Need For Speed Most Wanted Black Edition'
X360 = ASSETS / 'x360/files'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'recomp'))
import recomp

def main():
    with tempfile.TemporaryDirectory(prefix='nfsmw-nis-') as directory:
        scratch = Path(directory)
        pe = recomp.PE()
        image = bytearray(0x1000000)
        for _, _, va, size, raw in pe.sections:
            image[pe.base + va:pe.base + va + size] = pe.data[raw:raw + size]
        (scratch / 'image.bin').write_bytes(image)
        source = (ROOT / 'recomp/out/speed_55.c').read_text()
        functions = {m[1]: m[0] for m in re.finditer(r'void f_([0-9A-F]{8})\(Cpu \*restrict c\) \{.*?\n}', source, re.S)}
        seen, todo = set(), ['00828DDE','00828773','008287AE','00828976']
        while todo:
            a = todo.pop()
            if a in seen: continue
            seen.add(a)
            todo.extend(set(re.findall(r'f_([0-9A-F]{8})\(c\)', functions[a])) - seen)
        (scratch / 'original.c').write_text('#include "runtime.h"\n' + '\n'.join('void f_' + a + '(Cpu *c);' for a in sorted(seen))
                                            + '\n' + '\n'.join(functions[a] for a in sorted(seen)))
        binary = scratch / 'test'
        r = subprocess.run(['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g', '-w', '-fsanitize=address,undefined',
            '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'recomp/out'),
            str(ROOT / 'runtime/x360copspeech.c'), str(ROOT / 'runtime/x360nisaudio_test.c'), str(scratch / 'original.c'),
            str(ROOT / 'runtime/settings.c'), str(ROOT / 'runtime/offline_stubs.c'), '-lz', '-o', str(binary)], capture_output=True, text=True)
        if r.returncode: print(r.stdout[-3000:] + r.stderr[-3000:]); sys.exit(1)
        args = [str(binary), str(scratch / 'image.bin'), str(X360), str(PC)] + (['broken'] if 'broken' in sys.argv else [])
        r = subprocess.run(args, capture_output=True, text=True)
        print(r.stdout[-3000:], r.stderr[-1500:], end='')
        sys.exit(r.returncode)
main()
