#!/usr/bin/env python3
"""Offline proof for audit2 #3 (NIS stream switch early): production nis_translate on a synthetic map shaped like retail
(stream 0 is 100 bytes longer in the X360 BIG than its PC extent). Sequential reads of stream 0 across the next PC stream's
start must stay on stream 0; a real jump to that start begins stream 1.
Result: PASS (fixed). `--old` (pinned pre-fix nis_translate): FAIL (1), the read at PC 1000 switches to stream 1.
Run: tools/.venv/bin/python -I runtime/x360nis_translate_test.py [--old]"""
from pathlib import Path
import hashlib
import shutil, subprocess, sys, tempfile
ROOT = Path(__file__).resolve().parents[1]

# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_TRANSLATE = r'''static int nis_translate(size_t pos, size_t *xpos, int *started, unsigned *which) {
    *started=0;
    int found=-1;
    pthread_mutex_lock(&nis_lock);
    for (unsigned i=0;i<142;i++) if (nis_map[i].ps==pos) { found=(int)i; *started=1; break; }
    if (found>=0) {
        unsigned k=0; for (unsigned i=0;i<nis_nactive;i++) if (nis_active[i]!=(unsigned)found) nis_active[k++]=nis_active[i];
        if (k==8) { memmove(nis_active,nis_active+1,7*sizeof *nis_active); k=7; }
        nis_active[k++]=(unsigned)found; nis_nactive=k;
    } else {
        for (unsigned i=nis_nactive;i-- && found<0;) {
            const NisMap *m=&nis_map[nis_active[i]];
            if (pos>=m->ps && pos-m->ps<m->xlen) found=(int)nis_active[i];
        }
        if (found<0) for (unsigned i=0;i<142;i++) if (pos>=nis_map[i].ps && pos<nis_map[i].pe) { found=(int)i; break; }
    }
    pthread_mutex_unlock(&nis_lock);
    if (found<0) return 0;
    *xpos=nis_map[found].xs+(pos-nis_map[found].ps); *which=(unsigned)found;
    return 1;
}'''
assert hashlib.sha256(OLD_TRANSLATE.encode()).hexdigest() == '8831a35c0bda7384f589e88db5776f928e1359631debb3cf1e6a0bcba00c80a4', 'pinned baseline changed'

old = '--old' in sys.argv
with tempfile.TemporaryDirectory(prefix='nfsmw-nistr-') as d:
    d = Path(d)
    shutil.copy(ROOT / 'runtime/x360nis_translate_test.c', d)
    if old:
        src = (ROOT / 'runtime/x360nisaudio.c').read_text()
        start = src.index('static int nis_translate(')
        end = src.index('\n}', start)+2
        src = src[:start]+OLD_TRANSLATE+src[end:]
        src = src.replace('nis_translate(pos,n,&xpos,&started,&which)', 'nis_translate(pos,&xpos,&started,&which)')
        (d / 'x360nisaudio.c').write_text(src)
    else:
        shutil.copy(ROOT / 'runtime/x360nisaudio.c', d)
    r = subprocess.run(['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g', '-w', '-fsanitize=address,undefined'] + (['-DOLD_SIG'] if old else []) +
        ['-I', str(d), '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'recomp/out'),
         str(d / 'x360nis_translate_test.c'), str(ROOT / 'runtime/x360copspeech.c'), str(ROOT / 'runtime/settings.c'), str(ROOT / 'runtime/offline_stubs.c'), '-lz', '-o', str(d / 't')],
        capture_output=True, text=True)
    if r.returncode: print(r.stderr[-3000:]); sys.exit(2)
    r = subprocess.run([str(d / 't')], capture_output=True, text=True)
    print(r.stdout, r.stderr[-1500:], end='')
    sys.exit(r.returncode)
