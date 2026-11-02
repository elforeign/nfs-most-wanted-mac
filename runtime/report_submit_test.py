"""Fake CLI/task/clock checks: fixed 207 checks/0 failures; predecessor 188/3.
Account-switch proof executes the production adapter with fake tasks; 16 broken controls rejected.
No real installed CLI or network. The driver prints actual good/broken counts for every control.
"""
from pathlib import Path
import os
import runpy
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'


def run(base, name, mutation=None):
    work = base / name
    work.mkdir()
    for filename in ('report_submit.m', 'report_submit.h', 'report_submit_test.m'):
        (work / filename).write_text((RUNTIME / filename).read_text())
    predecessor = name == 'predecessor'
    if predecessor:
        fixture = runpy.run_path(str(RUNTIME / 'report_github_baseline.py'))
        source = fixture['SOURCES']['report_submit.m']
        assert hashlib.sha256(source.encode()).hexdigest() == fixture['SHA256']['report_submit.m']
        (work / 'report_submit.m').write_text(source)
        path = work / 'report_submit_test.m'
        source = path.read_text().replace('ReportRawTaskRunner runner = ReportRawRunner(', 'ReportTaskRunner runner = ReportRunner(')
        source = source.replace('], input, 1, ReportChildEnvironment(),', '], input, 1,')
        source = source.replace('CredentialFailureTests();', '')
        path.write_text(source)
    if mutation:
        old, new = mutation
        path = work / 'report_submit.m'
        source = path.read_text()
        assert source.count(old) == (2 if name == 'adapter_output_bound' else 1), (name, source.count(old))
        path.write_text(source.replace(old, new))
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter',
               '-fsanitize=address,undefined', '-fobjc-arc', '-DNFS_PUBLIC=1', str(work / 'report_submit_test.m'),
               '-framework', 'Foundation', '-o', str(work / 'check')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, name + result.stderr[-4000:]
    result = subprocess.run([str(work / 'check')], capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
    print(name + ': ' + result.stdout.strip(), flush=True)
    counts = re.search(r'report submit: (\d+) checks, (\d+) failures; fake creations (\d+)', result.stdout)
    assert counts, name + ' did not finish behaviorally: ' + result.stderr[-4000:]
    assert result.returncode == (1 if mutation or predecessor else 0) and (int(counts[2]) > 0) == bool(mutation or predecessor), result.stderr[-4000:]


def main():
    mutations = {
        'post_without_identity': ('_state != ReportSubmitReady || !_account.length', '_state == ReportSubmitPosting'),
        'identity_recheck': ('![ReportAccount(output) isEqual:account]', 'NO'),
        'stale_identity': ('generation != self->_generation || self->_state != ReportSubmitChecking', 'NO'),
        'late_creation': ('creationGeneration != self->_generation || self->_state != ReportSubmitPosting) return;\n                        NSString *url', 'NO) return;\n                        NSString *url'),
        'wrong_repo': ('com/elforeign/nfs-most-wanted-mac/issues/[1-9]', 'com/(?:elforeign/nfs-most-wanted-mac|wrong/repo)/issues/[1-9]'),
        'stderr_leak': ('@"GitHub identity unavailable. CLI may be missing, signed out or offline. Copy text & open GitHub is available."', '[[NSString alloc] initWithData:output encoding:NSUTF8StringEncoding]'),
        'identity_only': ('@"GH_NO_UPDATE_NOTIFIER":@"1"', '@"GH_NO_UPDATE_NOTIFIER":@"0"'),
        'prompts': ('@"GH_PROMPT_DISABLED":@"1"', '@"GH_PROMPT_DISABLED":@"0"'),
        'token_environment': ('@"NO_COLOR":@"1"', '@"NO_COLOR":@"1", @"GH_TOKEN":@"secret"'),
        'attachment_claim': ('BOOL ReportAutomaticAttachmentsSupported(void) { return NO; }', 'BOOL ReportAutomaticAttachmentsSupported(void) { return YES; }'),
        'identity_callback_once': ('NSUInteger creationGeneration = ++self->_generation;', 'NSUInteger creationGeneration = self->_generation;'),
        'adapter_timeout': ('if (uncertain || now() >= end)', 'if (uncertain)'),
        'adapter_output_bound': ('if (output.length + (NSUInteger)n > 4096) { uncertain = YES; break; }', 'if (output.length + (NSUInteger)n > 65536) { uncertain = YES; break; }'),
        'scripted_post': ('if (getenv("BOOT_KEYS") || _state != ReportSubmitReady', 'if (NO || _state != ReportSubmitReady'),
        'immutable': ('[snapshot[@"body"] copy]', 'snapshot[@"body"]'),
        'scripted': ('if (getenv("BOOT_KEYS") || !(_state', 'if (NO || !(_state'),
    }
    # Tests must not substitute a shell transport or expose child stderr/environment.
    source = (RUNTIME / 'report_submit.m').read_text()
    assert 'task.standardError = NSFileHandle.fileHandleWithNullDevice' in source
    assert 'task.environment = environment' in source
    assert 'task.arguments = arguments' in source
    assert 'now() >= end' in source and 'output.length + (NSUInteger)n > 4096' in source
    assert 'task.currentDirectoryURL = [NSURL fileURLWithPath:@"/"]' in source
    with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache', prefix='report-submit-') as directory:
        base = Path(directory)
        run(base, 'good')
        run(base, 'predecessor')
        for name, mutation in mutations.items():
            run(base, name, mutation)
    print(f'PASS: report submit; {len(mutations)} broken controls rejected; no real CLI')


if __name__ == '__main__':
    main()
