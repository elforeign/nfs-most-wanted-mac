"""Offline test for audit2 #10: threads.c kept only 64 pending host->guest event signals and silently dropped the rest.
Compiles production threads.c with async_events_test.c: 200 distinct events signaled from the host side before the guest
applies them. New code: signaled=200 of 200 -> PASS. Old code (threads.c from main before this fix; pass its path as argv[1],
e.g. `git show main:runtime/threads.c > /tmp/old_threads.c`): signaled=64 of 200 -> FAIL.
Run: $TOOLS_PY -I runtime/async_events_test.py [threads.c path]
"""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
src = sys.argv[1] if len(sys.argv) > 1 else HERE + '/threads.c'
d = tempfile.mkdtemp()
exe = os.path.join(d, 't')
subprocess.check_call(['clang', '-w', '-O1', '-I', HERE, '-I', HERE + '/../recomp', '-I', HERE + '/../recomp/out',
                       '-DTHREADS_C="%s"' % src, HERE + '/async_events_test.c', '-o', exe])
r = subprocess.run([exe], capture_output=True, text=True)
print(r.stdout.strip())
print('PASS' if r.returncode == 0 else 'FAIL')
sys.exit(r.returncode)
