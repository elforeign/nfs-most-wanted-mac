#!/usr/bin/env python3
"""Physical atmosphere LUT proof, analytic off-grid phases, cache and live fog.
Original angular interpolation: 48,452 checks / 7,139 failures, max 31.238194%.
Analytic phase: 48,452 / 0 failures, max 0.000167332%; swapped: 48,452 / 35,822.
The coarse-phase mutation recreates the original 65-angle interpolation error.
No guest emulation, GPU or private assets.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='atmosphere-') as temporary:
    for label, flags in [('physical', []), ('swapped', ['-DATMOSPHERE_TEST_SWAPPED']), ('coarse', [])]:
        exe = Path(temporary) / label
        source = ROOT/'runtime/atmosphere.m'
        if label == 'coarse':
            body = source.read_text()
            phase = '''double rayleigh=3.0/(16*pi)*(1+mu*mu);
    double denom=1+g*g-2*g*mu;
    double hg=(1-g*g)/(4*pi*denom*sqrt(denom));'''
            coarse = '''double aa=(mu+1)*32, af=aa-floor(aa), rayleigh=0, hg=0;
    for (unsigned i=0;i<2;++i) {
        double m=-1+fmin(64,floor(aa)+i)/32, w=i?af:1-af;
        double den=1+g*g-2*g*m;
        rayleigh+=w*3/(16*pi)*(1+m*m);
        hg+=w*(1-g*g)/(4*pi*den*sqrt(den));
    }'''
            assert phase in body
            source = Path(temporary)/'coarse.m'
            source.write_text(body.replace(phase, coarse))
        p = subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'runtime'),
                            '-I', str(ROOT/'recomp'), *flags, str(ROOT/'runtime/atmosphere_test.m'),
                            str(source), '-o', str(exe)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-4000:]
        p = subprocess.run([exe], capture_output=True, text=True)
        print(label + ': ' + p.stdout.strip(), flush=True)
        assert p.returncode == (0 if label == 'physical' else 1), (p.returncode, p.stderr[-4000:])
        assert 'failures=0' in p.stdout if label == 'physical' else re.search(r'failures=[1-9]\d*', p.stdout)
        if label == 'coarse':
            assert 'failures=7139' in p.stdout
print('atmosphere_test: PASS (analytic references pass; swapped/coarse phases rejected)')
