"""Offline test of steerresp.c (F10 Controls > Steering response): the three curves as the DoHumanSteering hook leaves them in
guest memory, against the retail SteerInputRemapTables table 0 data read from the PC exe (21 floats at 008F8B80).
Retail must equal the exe's table exactly; Medium = (table + x) / 2 at each point; Linear = x; lookups at -1,-.5,-.25,0,.05,.3,.5,.7,.9,1
go through the game's own interpolation (Table::GetValue 006533B0). The hook must run the original every call.
Run:  $TOOLS_PY -I runtime/steerresp_test.py   (from the checkout)
Result: PASS. Broken run (hook not applied, -DNO_SYNC): FAIL, Medium and Linear still return the retail points.
"""
import os, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(os.environ.get('NFS_GAME') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Need For Speed Most Wanted Black Edition'), 'speed.exe')
INS = [-1, -.5, -.25, 0, .05, .3, .5, .7, .9, 1]


def exe_table():
    b = open(EXE, 'rb').read()
    pe = struct.unpack_from('<I', b, 0x3c)[0]
    ns, osz = struct.unpack_from('<H', b, pe + 6)[0], struct.unpack_from('<H', b, pe + 20)[0]
    base = struct.unpack_from('<I', b, pe + 52)[0]
    for i in range(ns):
        _, vs, va, rs, ro = struct.unpack_from('<8sIIII', b, pe + 24 + osz + 40 * i)
        if va <= 0x8F8B80 - base < va + rs:
            o = ro + 0x8F8B80 - base - va
            return b[o:o + 84]


def lookup(d, v):
    t = (v + 1.0) * 10.0
    k = int(t)
    if k < 0 or t < 0: return d[0]
    if k >= 20: return d[20]
    f = t - k
    return f * d[k + 1] + (1 - f) * d[k]


def main():
    raw = exe_table()
    retail = struct.unpack('<21f', raw)
    assert abs(retail[15] - .148) < 1e-6 and retail[20] == 1.0
    out = os.path.join(tempfile.mkdtemp(), 'st')
    defs = ['-DNO_SYNC'] if '--broken' in sys.argv else []
    subprocess.check_call(['cc', '-std=gnu11', '-O1', '-I', HERE, '-I', os.path.join(HERE, '..', 'recomp'), *defs,
                           os.path.join(HERE, 'steerresp_test.c'), '-o', out])
    p = subprocess.run([out, raw.hex()],
                       capture_output=True, text=True)
    rows = [l.split('|') for l in p.stdout.splitlines()]
    fails = 0
    want = [list(retail), [(r + (i - 10) / 10) / 2 for i, r in enumerate(retail)], [(i - 10) / 10 for i in range(21)]]
    for mode, (pts, lk) in enumerate(rows):
        got = [float(x) for x in pts.split()[1:]]
        w = want[mode]
        bad = sum(abs(a - b) > 1e-6 for a, b in zip(got, w))
        if mode == 0: bad += got != [round(x, 6) for x in retail]   # the exe's floats (printed to 6 places)
        lg = [float(x) for x in lk.split()]
        lw = [lookup(w, v) for v in INS]
        bad += sum(abs(a - b) > 1e-5 for a, b in zip(lg, lw))
        print('Retail Medium Linear'.split()[mode], 'half stick ->', round(lg[6], 4), 'points off:', bad)
        fails += bad > 0
    if p.returncode: print('hook did not call the original every time'); fails += 1
    print('PASS' if not fails else 'FAIL, %d modes wrong' % fails)
    return 1 if fails else 0


sys.exit(main())
