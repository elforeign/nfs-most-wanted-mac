"""Offline test of controltype.c: its Control Type table against the X360 retail data, read fresh.

For each type 1-10 the table index the C code computes is looked up in the PC exe's drive table 0089F6AC (the same ten
keys as the X360's 8206247C, input_parity.md), that key's collection is decoded from the X360 ATTRIBUTES.BIN (parent
inheritance resolved), and every action the type overrides must name the X360 record's source; analog-source ramps
(right stick, triggers) must equal the X360 low, binary sources carry none, left-stick steering keeps the dead zone row.
Run:  $TOOLS_PY -I runtime/controltype_test.py   (from the checkout)
Result: PASS, 120 checks. Broken table (variant 2 gas "A" -> "B"): FAIL, 2 failures (Types 2, 7); broken type read
(ct_table_index without + leftStick): FAIL, 8 failures (steering of Types 1-10).
"""
import os, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools_snapshot', 'vaults'))
import compare_vaults as cv   # noqa: E402
from vaultlib import hash32   # noqa: E402

EXE = os.path.join(os.environ.get('NFS_GAME') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Need For Speed Most Wanted Black Edition'), 'speed.exe')
NAMES = {0: 'GAMEACTION_GAS', 1: 'GAMEACTION_BRAKE', 2: 'GAMEACTION_STEERLEFT', 3: 'GAMEACTION_STEERRIGHT',
         4: 'GAMEACTION_HANDBRAKE', 5: 'GAMEACTION_GAMEBREAKER', 6: 'GAMEACTION_NOS', 7: 'GAMEACTION_SHIFTDOWN',
         8: 'GAMEACTION_SHIFTUP', 9: 'GAMEACTION_RESET', 15: 'CAMERAACTION_CHANGE', 16: 'CAMERAACTION_LOOKBACK'}
X360 = os.environ.get('NFS_X360_FILES') or os.path.join(os.path.dirname(os.path.dirname(EXE)), 'x360', 'files')
def _steer_low():   # the X360 steering dead zone the build generated from the player's ATTRIBUTES.BIN
    inc = os.path.join(HERE, 'gen', 'x360_engine.inc')
    w = int(next(l.split()[2] for l in open(inc) if l.startswith('#define X360E_STEER_LOW_WORD')).rstrip('u'), 16)
    return struct.unpack('<f', struct.pack('<I', w))[0]


STEER_LOW = _steer_low()
ANALOG = {'ARY_U', 'ARY_D', 'L', 'R', 'ALX_L', 'ALX_R'}


def exe_u32(va):
    b = open(EXE, 'rb').read()
    pe = struct.unpack_from('<I', b, 0x3c)[0]
    ns, osz = struct.unpack_from('<H', b, pe + 6)[0], struct.unpack_from('<H', b, pe + 20)[0]
    base = struct.unpack_from('<I', b, pe + 52)[0]
    for i in range(ns):
        _, vs, va_, rs, ro = struct.unpack_from('<8sIIII', b, pe + 24 + osz + 40 * i)
        if va_ <= va - base < va_ + max(vs, rs):
            return struct.unpack_from('<I', b, ro + va - base - va_)[0]


def x360_records():
    pc = cv.Side(os.path.join(os.path.dirname(EXE), 'GLOBAL'), 'PC', '<')
    xb = cv.Side(os.path.join(X360, 'GLOBAL'), 'X360', '>')
    shared = dict(pc.types)
    shared.update(xb.types)
    xb.parse(shared)
    C = {k: c for (f, v, cls, k), c in xb.colls.items() if f == 'ATTRIBUTES.BIN'}

    def get(key, action):
        while key in C:
            f = C[key]['fields'].get(hash32(action))
            if f:
                w = f[2][0][1]
                raw = b''.join(x[1] if x[0] == 'r' else b'\0\0\0\0' for x in w)
                lo, hi = struct.unpack_from('>2f', raw, 20)
                return (w[3][1] if w[3][0] == 'p' else None), round(lo, 3), round(hi, 3)
            key = C[key]['parent']
        return None
    return get


def main():
    if not os.path.exists(os.path.join(X360, 'GLOBAL', 'ATTRIBUTES.BIN')):
        print('SKIP: no X360 ATTRIBUTES.BIN under %s' % X360)
        return 3
    exe = '/tmp/controltype_test_%d' % os.getpid()
    subprocess.check_call(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-DCONTROLTYPE_TEST', '-I', HERE,
                           os.path.join(HERE, 'controltype_test.c'), '-o', exe])
    rows = subprocess.check_output([exe], text=True).split('\n')
    os.unlink(exe)
    get = x360_records()
    checks = fails = 0
    for line in filter(None, rows):
        t, idx, a, src, low, known = line.split()
        t, idx, a, low, known = int(t), int(idx), int(a), float(low), int(known)
        key = exe_u32(0x0089F6AC + 4 * idx)
        rec = get(key, NAMES[a])
        xsrc, xlo, xhi = rec if rec else (None, 0, 0)
        if src == '-':
            # the port's own bindings: only Type 1 steering and variant 1's buttons (Types 1 / 6)
            ok = (t in (1, 6) and a not in (2, 3)) or (t == 1 and a in (2, 3))
            why = 'port bindings'
        elif xsrc in ('ALX_L', 'ALX_R'):
            ok = src == xsrc and low == 0 and abs(xlo - STEER_LOW) < 1e-6 and known   # the dead zone row gives the generated X360 low
            why = 'left stick, ramp from the dead zone row'
        else:
            want_low = xlo if xsrc in ANALOG else 0.0
            ok = src == xsrc and abs(low - want_low) < 1e-3 and xhi == 0 and known
            why = 'X360 %s low %.2f' % (xsrc, xlo)
        checks += 1
        if not ok:
            fails += 1
            print('FAIL type %d (index %d, key %08X) %s: C %s %.2f vs %s' % (t, idx, key, NAMES[a], src, low, why))
    print('%s, %d checks, %d failures' % ('PASS' if not fails else 'FAIL', checks, fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
