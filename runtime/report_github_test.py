"""Public report destinations, clipboard, privacy, consent and actual F10/controller input; no desktop/network/game.

Proof: public 219 checks/0 failures; private 6/0; all 38 broken controls rejected.
Old join-code redaction (IP rules retained): 11 failures; unsanitized bundle export: 60 failures (six text attachments).
Old IPv4 sentence boundary: 7 failures (preview plus all six bundle attachments).
Case-insensitive compact/formatted and all-letter checksum-valid codes leave zero leaks; display/perf text survives.
Pre-fix branch: 28 failures vs production 0, including te2943a2w and ABCDEFGAU in previews/six attachments.
IPv4/IPv6 rule removal: 18/16 failures; both destinations and the bundle use the shared endpoint rules.
Missing checksum/time checks: 7 failures each; compact-letter guard: 7; uppercase-only: 14.
Every privacy rule has its own removal control, including each known identity class.
The runtime suite discovers this driver and its paired Objective-C fixture automatically.
"""
import os
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
    'length': ('report_github.m', 'static const NSUInteger IssueLimit = 7500;', 'static const NSUInteger IssueLimit = 75000;'),
    'encoding': ('report_github.m', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~&?# +'),
    'consent': ('report_github.m', 'active = 1; confirmSelected = 0;', 'OpenBrowser([NSURL URLWithString:issueURL]); active = 1; confirmSelected = 0;'),
    'network': ('report_github.m', '    (void)unused;', '    (void)unused; [[[NSURLSession sharedSession] dataTaskWithURL:[NSURL URLWithString:IssueBase]] resume];'),
    'cancel_default': ('report_github.m', 'active = 1; confirmSelected = 0;', 'active = 1; confirmSelected = 1;'),
    'preview': ('report_github.m', 'NSString *text = issueBody;', 'NSString *text = @"different text";'),
    'priority': ('report_github.m', 'if (notable.length) {', 'if (0 && notable.length) {'),
    'keyboard': ('portmenu.m', 'if (report_github_active()) { report_github_key(vk);', 'if (0 && report_github_active()) { report_github_key(vk);'),
    'controller': ('portmenu.m', 'if (report_github_active()) { report_github_pad(0, edge);', 'if (0 && report_github_active()) { report_github_pad(0, edge);'),
    'close': ('portmenu.m', 'if (!o) report_github_close();', 'if (0) report_github_close();'),
    'folder': ('report_github.m', 'portmenu_open_folder(issueFolder.fileSystemRepresentation);', '(void)issueFolder;'),
    'discord_clipboard': ('report_github.m', '[pasteboard setString:text forType:NSPasteboardTypeString]', '[pasteboard setString:[text stringByAppendingString:@" different"] forType:NSPasteboardTypeString]'),
    'discord_limit': ('report_github.m', 'static const NSUInteger DiscordLimit = 2000;', 'static const NSUInteger DiscordLimit = 20000;'),
    'discord_invite': ('report_github.m', '@"https://discord.gg/VYKkrVhMrd"', '@"https://discord.gg/wrong"'),
    'discord_absent': ('report_github.m', 'settings_add(&s_discord);', '(void)s_discord;'),
    'discord_copy_failure': ('report_github.m', 'discord && !CopyReport(issueBody)', 'discord && !CopyReport(issueBody) && 0'),
    'discord_mouse': ('report_github.m', 'else if (x >= 240 && x < 440) Confirm(1);', 'else if (x >= 240 && x < 440) Confirm(0);'),
    'discord_controller': ('report_github.m', 'Confirm(confirmSelected == 2)', 'Confirm(0)'),
    'private': ('report_github.m', '#if defined(NFS_PUBLIC) && NFS_PUBLIC', '#if 1'),
}


def build_and_run(base, name, mutation=None, public=True):
    work = base / name
    work.mkdir()
    for filename in ('report_github.m', 'report_github.h', 'report_github_test.m', 'portmenu.m', 'report_privacy.h'):
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
        path.write_text(source)
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-Wno-unused-variable', '-fsanitize=address,undefined', '-fobjc-arc', f'-DNFS_PUBLIC={int(public)}',
               '-I' + str(ROOT / 'recomp'), '-I' + str(ROOT / 'recomp/out'), '-I' + str(RUNTIME),
               str(work / 'report_github_test.m'), str(RUNTIME / 'settings.c'), str(RUNTIME / 'offline_stubs.c'),
               '-framework', 'Cocoa', '-framework', 'CoreText', '-framework', 'QuartzCore', '-framework', 'Metal',
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
    # The only external action in production is NSWorkspace's URL opener. Block added transports.
    forbidden = r'NSURLSession|NSURLConnection|CFHTTP|CFReadStream|\bNSTask\b|\b(?:socket|connect|sendto|send|curl|system|popen)\s*\('
    assert not re.search(forbidden, source), 'issue preparation must not have a network/process transport'
    assert 'report_github.m' in (RUNTIME / 'build.sh').read_text().split('else', 1)[0]
    with tempfile.TemporaryDirectory(prefix='github-report-') as directory:
        base = Path(directory)
        build_and_run(base, 'good_public')
        build_and_run(base, 'good_private', public=False)
        for name, mutation in MUTANTS.items():
            build_and_run(base, name, mutation, public=name != 'private')
    print('PASS: GitHub/Discord report URL/clipboard/privacy/consent and F10 input; all broken controls rejected')


if __name__ == '__main__':
    main()
