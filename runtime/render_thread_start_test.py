"""Render thread start failure: production render_thread.m with pthread_create forced to fail must hand out direct
queue buffers (rt_on 0). The original (result ignored) keeps recording for a thread that never started, so Present
would wait forever: that control prints recorder=1 rt_on=1 and exits 1; the fixed source prints recorder=0 rt_on=0, exit 0.
"""
from pathlib import Path
import subprocess
import tempfile

RUNTIME = Path(__file__).resolve().parent
ROOT = RUNTIME.parent
production = (RUNTIME / 'render_thread.m').read_text()
FIXED = '''    int err = pthread_create(&t, &at, render_thread, NULL);
    pthread_attr_destroy(&at);
    if (err) {            // no consumer: encode directly instead of waiting on buffers nothing will replay
        rt_on = 0;
        fprintf(stderr, "boot: render: render thread failed to start (%d), encoding on the calling thread\\n", err);
        return;
    }
'''
ORIGINAL = '    pthread_create(&t, &at, render_thread, NULL);\n    pthread_attr_destroy(&at);\n'
assert production.count(FIXED) == 1, 'production start-failure fallback missing'


def build_run(directory, name, source, expected):
    src = directory / (name + '.m')
    src.write_text(source)
    exe = directory / name
    subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Wno-unused-function', '-fobjc-arc', '-I', RUNTIME,
                    '-I', ROOT / 'recomp', f'-DRT_RECORDER_SOURCE="{src}"', RUNTIME / 'render_thread_start_test.m',
                    '-framework', 'Metal', '-framework', 'Foundation', '-o', exe], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    print(name, r.returncode, r.stdout.strip(), flush=True)
    assert r.returncode == expected and r.stdout.strip() == ('recorder=0 rt_on=0' if expected == 0 else 'recorder=1 rt_on=1'), r


with tempfile.TemporaryDirectory(prefix='rt-start-') as d:
    d = Path(d)
    build_run(d, 'fixed', production, 0)
    build_run(d, 'original', production.replace(FIXED, ORIGINAL), 1)
print('PASS render thread start failure falls back to direct submission')
