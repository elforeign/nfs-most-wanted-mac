"""Public report review, consent, privacy and actual F10 input; fake transports only.

Proof: public 401 checks/0 failures; private 6/0; 49 broken controls rejected.
Pinned original 13984ab7: 16 checks/11 failures, including fake creation, fallback copy,
constant fallback URL, diagnostics, account/public warning, screenshot review and fresh input.
Archive regression without history: predecessor 1 failure/0 completed proofs; fixed 0 failures/1 completed proof.
The predecessor requests 1 private object; the fixed proof makes 0 history lookups.
Pinned source and SHA-256 digests ship in report_github_baseline.py; behavioral logs stay under
.metal-cache/report-submit/original-proof/.
Applicable identity/endpoint/bundle and F10 input controls are retained. The removed query builder's
encoding/URL-size controls are replaced by constant-URL, Unicode/body-bound and exact-copy checks.
Every mutation must finish with failing behavioral assertions; compile failures/crashes do not count.
The offline suite discovers this driver and its paired Objective-C fixture automatically.
Room-code checks cover both destination snapshots and all six text attachments.
Both sides' large-input cases use the snapshot section/body bounds and constant fallback URL.

"""
import os
import runpy
from unittest.mock import patch
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'

MUTANTS = {
    'ipv4_sentence': ('report_privacy.h', '(?![A-Za-z0-9]|\\\\.[0-9])', '(?![A-Za-z0-9.])'),
    'join_codes': ('report_github.m', 'return ReportEndpointRedact(text);', 'return text;'),
    'bundle': ('report_privacy.h', 'if (!text || ![ReportEndpointRedact(text) writeToFile:', 'if (!text || ![text writeToFile:'),
    'home': ('report_github.m', 'if (home.length)', 'if (0 && home.length)'),
    'generic_home': ('report_github.m', 'text = ReplacePattern(text, @"/(?:Users|home)/[^/\\\\s]+", @"~");', '(void)text;'),
    'user': ('report_github.m', '@[@"user", @"computer", @"serial", @"wifi"]', '@[@"computer", @"serial", @"wifi"]'),
    'computer': ('report_github.m', '@[@"user", @"computer", @"serial", @"wifi"]', '@[@"user", @"serial", @"wifi"]'),
    'serial_identity': ('report_github.m', '@[@"user", @"computer", @"serial", @"wifi"]', '@[@"user", @"computer", @"wifi"]'),
    'wifi_identity': ('report_github.m', '@[@"user", @"computer", @"serial", @"wifi"]', '@[@"user", @"computer", @"serial"]'),
    'consent': ('report_github.m', 'active = 1; confirmSelected = 0;', 'OpenBrowser([NSURL URLWithString:issueURL]); active = 1; confirmSelected = 0;'),
    'network': ('report_github.m', 'static void SendToGitHub(int discord) {', 'static void SendToGitHub(int discord) { [[[NSURLSession sharedSession] dataTaskWithURL:[NSURL URLWithString:IssueBase]] resume];'),
    'cancel_default': ('report_github.m', 'padArmed = !padHeld; confirmSelected = 0;', 'padArmed = !padHeld; confirmSelected = 1;'),
    'preview': ('report_github.m', 'NSString *text = issueBody;', 'NSString *text = @"different text";'),
    'keyboard': ('portmenu.m', 'if (report_github_active()) { report_github_key_event(vk, down, repeat);', 'if (0 && report_github_active()) { report_github_key_event(vk, down, repeat);'),
    'controller': ('portmenu.m', 'if (report_github_active()) { report_github_pad_release(phys); report_github_pad(0, edge);', 'if (0 && report_github_active()) { report_github_pad_release(phys); report_github_pad(0, edge);'),
    'close': ('portmenu.m', 'if (!o) report_github_close();', 'if (0) report_github_close();'),
    'folder': ('report_github.m', 'portmenu_open_folder(issueFolder.fileSystemRepresentation);', '(void)issueFolder;'),
    'discord_clipboard': ('report_github.m', '[pasteboard setString:text forType:NSPasteboardTypeString]', '[pasteboard setString:[text stringByAppendingString:@" different"] forType:NSPasteboardTypeString]'),
    'discord_invite': ('report_github.m', '@"https://discord.gg/VYKkrVhMrd"', '@"https://discord.gg/wrong"'),
    'discord_absent': ('report_github.m', 'settings_add(&s_discord);', '(void)s_discord;'),
    'discord_copy_failure': ('report_github.m', 'if (!CopyReport(issueBody))', 'if (NO && !CopyReport(issueBody))'),
    'discord_mouse': ('report_github.m', 'else if (x >= 600 && x < 800) Confirm(3);', 'else if (x >= 600 && x < 800) Confirm(2);'),
    'discord_controller': ('report_github.m', 'if ((edge & 1) && padArmed) { padArmed = NO; Confirm(confirmSelected); }', 'if ((edge & 1) && padArmed) { padArmed = NO; Confirm(0); }'),
    'section_bounds': ('report_github.m', 'discord ? 220 : 1400', 'discord ? 14000 : 14000'),
    'fallback_query': ('report_github.m', '@"https://github.com/elforeign/nfs-most-wanted-mac/issues/new"', '@"https://github.com/elforeign/nfs-most-wanted-mac/issues/new?body=unreviewed"'),
    'key_repeat': ('report_github.m', 'if (repeat && (key == 0x0D || key == 0x20)) return;', '(void)repeat;'),
    'mouse_release': ('report_github.m', 'if (!mouseArmed || wasHeld) return;', 'if (wasHeld) return;'),
    'shot_seen': ('report_github.m', 'if (!screenshotSeen) return;', 'if (NO && !screenshotSeen) return;'),
    'shot_metadata': ('report_github.m', 'frozenFrame = [clean representationUsingType:NSBitmapImageFileTypePNG properties:@{}];', 'frozenFrame = source;'),
    'snapshot_atomic': ('report_github.m', 'BOOL requestedDiscord = discord != 0;', 'if (!reuse) submission = nextSubmission; BOOL requestedDiscord = discord != 0;'),
    'display_rows': ('report_github.m', 'if ([key isEqual:@"display"] && fields[key])', 'if (NO && [key isEqual:@"display"] && fields[key])'),
    'read_error_visible': ('report_github.m', 'issueError = @"Couldn\'t safely read the saved report. Save a new report and retry.";', 'issueError = nil;'),
    'frame_available': ('report_github.m', 'if (age > 600) {', 'frameInfo = savedAge; if (age > 600) {'),
    'account_visible': ('report_github.m', 'if (submission.state == ReportSubmitReady && !accountSeen)', 'if (NO && submission.state == ReportSubmitReady && !accountSeen)'),
    'preview_clip': ('report_github.m', 'CGContextClipToRect(ctx, CGRectMake(viewport.origin.x, height - viewport.origin.y - viewHeight, viewport.size.width, viewHeight));', 'CGContextClipToRect(ctx, CGRectMake(viewport.origin.x, height - CGRectGetMaxY(viewport), viewport.size.width, viewHeight));'),
    'destination_atomic': ('report_github.m', 'BOOL requestedDiscord = discord != 0;', 'BOOL requestedDiscord = discord != 0; discordPreview = requestedDiscord;'),
    'diagnostic_allowlist': ('report_github.m', 'if ([numeric numberOfMatchesInString:line options:0 range:NSMakeRange(0, line.length)])', 'if (line.length)'),
    'private': ('report_github.m', '#if defined(NFS_PUBLIC) && NFS_PUBLIC', '#if 1'),
}


def build_and_run(base, name, mutation=None, public=True):
    work = base / name
    work.mkdir()
    for filename in ('report_github.m', 'report_github.h', 'report_github_test.m', 'portmenu.m', 'report_privacy.h', 'report_submit.m', 'report_submit.h'):
        shutil.copyfile(RUNTIME / filename, work / filename)
    if mutation:
        filename, old, new = mutation
        path = work / filename
        source = path.read_text()
        assert source.count(old) == 1, (name, source.count(old))
        source = source.replace(old, new)
        # This deliberately broken control needs the forward declaration to compile.
        if name == 'consent':
            source = source.replace('static void SendToGitHub', 'static BOOL OpenBrowser(NSURL *url);\nstatic void SendToGitHub', 1)
        if name == 'private':
            header = work / 'report_github.h'
            header.write_text(header.read_text().replace('#if defined(NFS_PUBLIC) && NFS_PUBLIC', '#if 1'))
            for extra in ('report_submit.m', 'report_submit.h'):
                extra_path = work / extra
                extra_path.write_text(extra_path.read_text().replace('#if defined(NFS_PUBLIC) && NFS_PUBLIC', '#if 1'))
        path.write_text(source)
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-Wno-unused-variable', '-Wno-unused-parameter', '-fsanitize=address,undefined', '-fobjc-arc', f'-DNFS_PUBLIC={int(public)}',
               '-I' + str(ROOT / 'recomp'), '-I' + str(ROOT / 'recomp/out'), '-I' + str(RUNTIME),
               str(work / 'report_github_test.m'), str(RUNTIME / 'settings.c'), str(RUNTIME / 'text_fit.c'), str(RUNTIME / 'offline_stubs.c'),
               '-framework', 'Cocoa', '-framework', 'CoreText', '-framework', 'ImageIO', '-framework', 'QuartzCore', '-framework', 'Metal',
               '-framework', 'SystemConfiguration', '-framework', 'IOKit', '-Wl,-dead_strip', '-o', str(work / 'check')]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert compiled.returncode == 0, name + ' compile failed:\n' + compiled.stderr[-6000:]
    profile = work / 'profile'; profile.mkdir()
    result = subprocess.run([str(work / 'check'), str(profile)], capture_output=True, text=True, timeout=45,
                            env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
    counts = re.search(r'github report: (\d+) checks, (\d+) failures; network requests (\d+)', result.stdout)
    assert counts, name + ' did not finish:\n' + (result.stdout + result.stderr)[-5000:]
    checks, failures, requests = map(int, counts.groups())
    print(f'{name}: {checks} checks, {failures} failures, {requests} network requests', flush=True)
    if mutation:
        assert result.returncode != 0 and failures > 0, name + ' mutation escaped detection'
    else:
        assert result.returncode == 0 and failures == 0 and requests == 0, result.stderr[-5000:]


def original_proof():
    import hashlib
    import json
    work = ROOT / '.metal-cache' / 'report-submit' / 'original-proof'
    work.mkdir(parents=True, exist_ok=True)
    fixture = runpy.run_path(str(RUNTIME / 'report_github_baseline.py'))
    digests = {}
    for filename in ('report_github.m', 'report_github.h', 'portmenu.m', 'report_privacy.h'):
        data = fixture['SOURCES'][filename].encode()
        assert hashlib.sha256(data).hexdigest() == fixture['SHA256'][filename]
        (work / filename).write_bytes(data)
        digests[filename] = hashlib.sha256(data).hexdigest()
    (work / 'digests.json').write_text(json.dumps({'fixture': 'report_github_baseline.py', 'sha256': digests}, indent=2) + '\n')
    (work / 'report_github_test.m').write_text((RUNTIME / 'report_github_test.m').read_text())
    command = ['clang', '-O1', '-g', '-Wno-unused-function', '-Wno-unused-variable', '-fobjc-arc',
               '-DNFS_PUBLIC=1', '-DREPORT_ORIGINAL_PROBES=1', '-I' + str(ROOT / 'recomp'),
               '-I' + str(ROOT / 'recomp/out'), '-I' + str(RUNTIME), str(work / 'report_github_test.m'),
               str(RUNTIME / 'settings.c'), str(RUNTIME / 'offline_stubs.c'), '-framework', 'Cocoa',
               '-framework', 'CoreText', '-framework', 'ImageIO', '-framework', 'QuartzCore', '-framework', 'Metal',
               '-framework', 'SystemConfiguration', '-framework', 'IOKit', '-Wl,-dead_strip', '-o', str(work / 'check')]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (work / 'compile.log').write_text(compiled.stdout + compiled.stderr)
    assert compiled.returncode == 0, compiled.stderr[-3000:]
    profile = work / 'profile'
    profile.mkdir(exist_ok=True)
    result = subprocess.run([str(work / 'check'), str(profile)], capture_output=True, text=True, timeout=30)
    (work / 'proof.log').write_text(result.stdout + result.stderr)
    counts = re.search(r'github report: (\d+) checks, (\d+) failures', result.stdout)
    assert counts and result.returncode == 1 and int(counts[2]) >= 10, result.stdout + result.stderr[-3000:]
    print('original 13984ab7: ' + counts[1] + ' checks, ' + counts[2] + ' failures (expected)', flush=True)


def main():
    assert 'ReportRedactBundle([NSString stringWithUTF8String:dir]);' in (RUNTIME / 'report.m').read_text()
    source = (RUNTIME / 'report_github.m').read_text()
    # Every generic privacy rule must fail independently when its production line is removed.
    markers = {'uuid': '@"[UUID]"', 'serial_label': '@"serial: [redacted]"', 'email': '@"[email]"',
               'wifi_label': '@"Wi-Fi: [redacted]"'}
    for name, marker in markers.items():
        lines = [line for line in source.splitlines() if marker in line and 'text = ReplacePattern' in line]
        assert len(lines) == 1, (name, lines)
        MUTANTS[name] = ('report_github.m', lines[0], '    (void)text;')
    privacy = (RUNTIME / 'report_privacy.h').read_text()
    # Reproduce the old preview redaction exactly for endpoints: retain IP masking, omit code masking.
    ip_patterns = [line.strip().rstrip(',') for line in privacy.splitlines() if line.strip().startswith('@"')][:2]
    ip_only = '\n'.join('    text = ReplacePattern(text, ' + pattern + ', @"[IP]");' for pattern in ip_patterns)
    MUTANTS['join_codes'] = ('report_github.m', 'return ReportEndpointRedact(text);', ip_only + '\n    return text;')
    for name, marker in {'ipv4': '(?:[0-9]{1,3}', 'ipv6': '(?:[0-9a-f]{0,4}:)'}.items():
        line = next(line for line in privacy.splitlines() if marker in line)
        MUTANTS[name] = ('report_privacy.h', line, '        @"^$",')
    room_line = next(line for line in privacy.splitlines() if 'R-[A-Za-z0-9]' in line)
    MUTANTS['room_codes'] = ('report_privacy.h', room_line, '        @"^$"')
    # Pin the original broad rules: ordinary words and times must reject both controls.
    ipv6_line = next(line for line in privacy.splitlines() if '(?:[0-9a-f]{0,4}:)' in line)
    MUTANTS['clock_times'] = ('report_privacy.h', ipv6_line,
        r'        @"(?<![A-Za-z0-9:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:.]*(?:%[a-z0-9]+)?(?![A-Za-z0-9:])",')
    code_line = next(line for line in privacy.splitlines() if '{4}-?' in line)
    MUTANTS['checksum_omitted'] = ('report_privacy.h',
        'if (ReportCodeChecksum([text substringWithRange:match.range]))', 'if (YES)')
    MUTANTS['compact_letters'] = ('report_privacy.h', code_line,
        code_line.replace('(?<![A-Za-z0-9-])', '(?<![A-Za-z0-9-])(?=[A-Z2-9-]{0,9}[2-9-])'))
    MUTANTS['uppercase_only'] = ('report_privacy.h',
        'options:NSRegularExpressionCaseInsensitive error:NULL',
        'options:i == 3 ? 0 : NSRegularExpressionCaseInsensitive error:NULL')
    # Preparation/UI must use only the consent-gated adapter; prohibit a second transport.
    forbidden = r'NSURLSession|NSURLConnection|CFHTTP|CFReadStream|\bNSTask\b|\b(?:socket|connect|sendto|send|curl|system|popen)\s*\('
    assert not re.search(forbidden, source), 'review must not bypass the submission adapter'
    assert 'report_github.m' in (RUNTIME / 'build.sh').read_text().split('else', 1)[0]
    # An exported source archive has no private Git objects. Exercise the production-source
    # proof with history access unavailable; the predecessor raises before any behavioral check.
    with patch.object(subprocess, 'check_output', side_effect=AssertionError('history access unavailable')) as history:
        original_proof()
        assert history.call_count == 0
    print('archive proof: 0 history lookups; pinned behavioral proof completed', flush=True)
    with tempfile.TemporaryDirectory(dir=ROOT / '.metal-cache', prefix='github-report-') as directory:
        base = Path(directory)
        build_and_run(base, 'good_public')
        build_and_run(base, 'good_private', public=False)
        for name, mutation in MUTANTS.items():
            build_and_run(base, name, mutation, public=name != 'private')
    print(f'PASS: GitHub/Discord snapshot/clipboard/privacy/consent and F10 input; {len(MUTANTS)} broken controls rejected')


if __name__ == '__main__':
    main()
