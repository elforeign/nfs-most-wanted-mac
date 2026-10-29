"""Offline test for audit2 #10: threads.c kept only 64 pending host->guest event signals and silently dropped the rest.
Also covers challenger item 5: a queued socket completion must not signal a closed/reused event handle.
Production: 200/200 signals and 6 generation checks, 0 failures (exit 0). Broken source controls:
disable pending-capacity growth -> 64/200 signals, 1 failure (exit 1); replace the generation equality
with 1 -> 200/200 signals and 6 generation checks, 2 failures (exit 1). Mutation details in the C header.
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
