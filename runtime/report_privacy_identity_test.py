"""Compile the bundle identity fixture against the shared redactor and the old rules."""
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

RUNTIME = Path(__file__).resolve().parent


def check(work, header, expected):
    work.mkdir()
    fixture = work / 'report_privacy_identity_test.m'
    shutil.copyfile(RUNTIME / fixture.name, fixture)
    (work / 'report_privacy.h').write_text(header)
    exe = work / 'check'
    subprocess.run(['clang', '-fobjc-arc', '-framework', 'Foundation', '-framework', 'IOKit',
                    '-framework', 'SystemConfiguration', str(fixture), '-o', str(exe)],
                   check=True, capture_output=True, timeout=90)
    data = work / 'data'
    data.mkdir()
    result = subprocess.run([str(exe), str(data)], capture_output=True, text=True, timeout=30)
    print(result.stdout.strip(), flush=True)
    count = re.search(r'(\d+) checks, (\d+) failures', result.stdout)
    assert count and tuple(map(int, count.groups())) == expected, result.stderr
    assert result.returncode == bool(expected[1]), result.stderr


def main():
    header = (RUNTIME / 'report_privacy.h').read_text()
    parts = header.index('    // Keep ordinary lowercase')
    end = header.index('    return text;', parts)
    broken = header[:parts] + header[end:]
    boundary = r'\\p{L}\\p{M}\\p{N}'
    start = broken.index('static NSString *ReportPrivacyKnownIdentity')
    end = broken.index('    return text;', start)
    broken = broken[:start] + broken[start:end].replace(boundary, r'\\p{L}\\p{N}_') + broken[end:]
    start = broken.index('    // Honor a UTF-16')
    end = broken.index('    NSMutableString *text = [NSMutableString string];', start)
    broken = broken[:start] + broken[end:]
    broken = broken.replace('count = c == 0 ? 0 : c < 0x80', 'count = c < 0x80')
    broken = broken.replace('if (c) [text appendFormat:', '[text appendFormat:')
    with tempfile.TemporaryDirectory(prefix='report-identity-') as path:
        work = Path(path)
        check(work / 'baseline', broken, (14, 10))
        check(work / 'fixed', header, (14, 0))


if __name__ == '__main__':
    main()
