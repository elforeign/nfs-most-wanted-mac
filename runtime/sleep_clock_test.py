"""Offline test of the production Sleep (runtime/threads.c) against audit2 #1: the deadline passing between two clock
reads must not underflow into a huge nanosleep. threads.c is compiled with a stubbed host_ticks (+0.6 ms per read) and
nanosleep; 1000 x Sleep(1); the largest requested nanosleep must be <= 1 ms.
Run:  $TOOLS_PY -I runtime/sleep_clock_test.py   (from the checkout)
Result: PASS with current threads.c: max_nanosleep_ns = 400,000 (1000 calls).
Broken run (threads.c from 0f08a0d^, the two-read loop): FAIL, max_nanosleep_ns = 18446744073709351616 (~1.8e10 s).
"""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))


def run(threads_c):
    d = tempfile.mkdtemp()
    exe = os.path.join(d, 't')
    cmd = ['clang', '-w', '-O1', '-I', HERE, '-I', HERE + '/../recomp', '-I', HERE + '/../recomp/out',
           '-DTHREADS_C="%s"' % threads_c, HERE + '/sleep_clock_test.c', '-o', exe]
    subprocess.check_call(cmd)
    out = subprocess.check_output([exe]).decode()
    return int(out.split('max_nanosleep_ns=')[1].split()[0]), out.strip()


if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else HERE + '/threads.c'
    mx, out = run(src)
    print(out)
    if mx <= 1000000:
        print('PASS'); sys.exit(0)
    print('FAIL: nanosleep requested %d ns' % mx); sys.exit(1)
