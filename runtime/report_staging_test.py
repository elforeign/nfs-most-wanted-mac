"""Interrupt the real report writer before redaction and inspect the reports directory.

Main: 7 checks, 4 failures. Fixed: 7/0. Visible staging control: 7/3.
"""
import fcntl
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

RUNTIME = Path(__file__).resolve().parent
SECRET = b'SyntheticStageSecret'


def check(source, work):
    build = runpy.run_path(str(RUNTIME / 'report_lock_test.py'))['run']
    exe = build(work / 'build', False, staging_source=source)
    profile = work / 'profile'
    reports = profile / 'reports'
    results = {}

    def run(arg):
        return subprocess.run([str(exe), str(profile), arg], capture_output=True, timeout=10,
                              env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))

    def visible():
        return {p.name for p in reports.iterdir() if not p.name.startswith('.')}

    result = run('--interrupt-report')
    assert result.returncode == 73, (result.returncode, result.stderr)
    results['interruption leaves no visible bundle'] = not visible()
    staged = list(reports.glob('.report-*'))
    results['raw crash attachment is confined to a private stage'] = (
        len(staged) == 1 and staged[0].stat().st_mode & 0o777 == 0o700
        and SECRET in (staged[0] / 'crash.txt').read_bytes()
    )
    # If staging is broken, exercise the reaper on a synthetic old stage anyway.
    stage = staged[0] if staged else reports / '.report-Abc123'
    stage.mkdir(exist_ok=True)
    os.utime(stage, (1, 1))
    fd = os.open(stage, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run('--reap-reports')
        results['reaper preserves an active stage'] = result.returncode == 0 and stage.exists()
    finally:
        os.close(fd)
    result = run('--reap-reports')
    results['reaper drops an interrupted stage'] = result.returncode == 0 and not stage.exists()

    before = visible()
    result = run('--redaction-failure')
    results['redaction failure publishes nothing'] = result.returncode == 0 and visible() == before
    before = visible()
    # These fixture saves have the same timestamp. Both must survive.
    result = run('--save-test-report')
    first = visible() - before
    results['successful redaction publishes a clean bundle'] = (
        result.returncode == 0 and len(first) == 1
        and SECRET not in (reports / next(iter(first)) / 'crash.txt').read_bytes()
    )
    result = run('--save-test-report')
    second = visible() - before
    results['publication keeps both same-second bundles'] = (
        result.returncode == 0 and len(second) == 2 and first < second
    )
    failures = [label for label, ok in results.items() if not ok]
    for label in failures:
        print('FAIL', label)
    print(f'report staging: {len(results)} checks, {len(failures)} failures', flush=True)
    return len(results), len(failures)


def main():
    with tempfile.TemporaryDirectory(prefix='report-stage-') as path:
        work = Path(path)
        if len(sys.argv) == 2:
            return bool(check(Path(sys.argv[1]).read_text(), work)[1])
        source = (RUNTIME / 'report.m').read_text()
        assert 'report_remove_stale_staging(reports_dir());' in source[source.index('void report_init(void)'):]
        prefix = '#define REPORT_STAGE_PREFIX ".report-"'
        assert source.count(prefix) == 1
        (work / 'fixed').mkdir()
        (work / 'broken').mkdir()
        assert check(source, work / 'fixed') == (7, 0)
        visible = source.replace(prefix, '#define REPORT_STAGE_PREFIX "report-"')
        assert check(visible, work / 'broken') == (7, 3)
    return 0


if __name__ == '__main__':
    sys.exit(main())
