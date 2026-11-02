#!/usr/bin/env python3
"""Keep offline baselines available without repository history.

Proof: 13 missed shell forms -> 0 findings before / 13 after (exit 1 each).
All 27 content forms, in shell and Python argv fixtures -> 54/54 rejected;
checkout with reviewed waivers -> exit 0, 0 unwaived findings.
Run with --inventory to list all reads, or --scan FILE... to check fixtures.
Waivers pin the complete set of commands in a file, so new reads in an already
waived file still fail. Pending entries are a repair queue, not new exemptions.
"""
import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ('runtime', 'recomp', 'host', 'media', 'tests', 'tools', 'tools_snapshot')
EXTENSIONS = {'.py', '.sh', '.c', '.h', '.cpp', '.m', '.mm', '.swift'}
SHELL = re.compile(r'''\bgit['"]?\s+(?:(?!\b(?:show|cat-file|archive|log)(?![\w-]))[^\n;|&])*?'''
                   r'''\b(show|cat-file|archive|log)(?![\w-])['"]?([^\n;|&]*)''')


def forbidden(op, args):
    # show defaults to HEAD's patch; REV:path is only one content form.
    # Treat the content commands conservatively even when flags suppress output.
    if op in ('show', 'cat-file', 'archive'):
        return True
    return op == 'log' and bool(re.search(
        r'''(^|[\s'"])(--(?:patch(?:-with-(?:raw|stat))?|cc|unified|binary|remerge-diff)(?:[\s'"]|=|$)'''
        r'|-(?![-SGnI])[^-\s]*[pucU]|-L)', args.split(' -- ', 1)[0]))


def shell_reads(text):
    text = text.replace('\\\n', ' ')
    for match in SHELL.finditer(text):
        op, args = match.groups()
        if forbidden(op, args):
            yield match.start(), ' '.join(match.group().split())


def reads(path):
    return text_reads(path.read_text(errors='replace'), path.suffix)


def text_reads(text, suffix):
    found = []
    if suffix == '.py':
        tree = ast.parse(text)
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        for node in ast.walk(tree):
            # Lists cover subprocess, Popen and local run wrappers, including
            # multiline commands, -C, computed revisions and concatenated paths.
            values = None
            if isinstance(node, (ast.List, ast.Tuple)):
                values = node.elts
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'git':
                values = [ast.Constant(value='git'), *node.args]
            if values:
                parts = [v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
                         else ast.unparse(v) for v in values]
                if parts[0] == 'git':
                    for i, op in enumerate(parts[1:], 1):
                        if op in ('show', 'cat-file', 'archive', 'log'):
                            if forbidden(op, ' '.join(parts[i + 1:])):
                                found.append((node.lineno, ' '.join(parts)))
                            break
            # shell=True, sh -c, and scripts generated inside Python tests.
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                for offset, command in shell_reads(node.value):
                    found.append((node.lineno + node.value[:offset].count('\n'), command))
                for offset, command in embedded_reads(node.value):
                    found.append((node.lineno + node.value[:offset].count('\n'), command))
    else:
        # Ignore prose comments, but keep strings (including C system calls).
        lines = [line if not line.lstrip().startswith(('#', '//', '*')) else ''
                 for line in text.splitlines()]
        clean = '\n'.join(lines)
        for offset, command in shell_reads(clean):
            found.append((clean[:offset].count('\n') + 1, command))
        for offset, command in embedded_reads(clean):
            found.append((clean[:offset].count('\n') + 1, command))
    return sorted(found)


def embedded_reads(text):
    # Generated Python scripts and Python heredocs inside shell drivers.
    for match in re.finditer(r'''\[\s*['"]git['"][^\]]*\]''', text):
        try:
            nested = text_reads(match.group(), '.py')
        except SyntaxError:
            continue
        for _, command in nested:
            yield match.start(), command


def sources(root):
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=root, capture_output=True)
    if result.returncode == 0:
        paths = [Path(p) for p in os.fsdecode(result.stdout).split('\0') if p]
    else:
        paths = [p.relative_to(root) for folder in ROOTS if (root / folder).exists()
                 for p in (root / folder).rglob('*') if p.is_file()]
    paths = {p for p in paths if p.parts[0] in ROOTS and p.suffix in EXTENSIONS
             and not any(x.startswith('.') or x in ('build', 'dist', 'out', '__pycache__', 'node_modules')
                         for x in p.parts) and (root / p).is_file()}
    selected = {p for p in paths if 'test' in p.stem or
                (p.suffix == '.sh' and p.name.startswith('build'))}
    selected.add(Path('runtime/offline_tests.sh'))
    # Follow source/script references too: a baseline hidden in a called build
    # helper is still a baseline. Resolve both root and caller-relative names.
    pending = list(selected)
    while pending:
        path = pending.pop()
        if not (root / path).is_file():
            continue
        text = (root / path).read_text(errors='replace')
        refs = re.findall(r'[\w./-]+\.(?:py|sh|cpp|c|mm|m|h|swift)\b', text)
        for ref in refs:
            for candidate in (Path(ref), Path(os.path.normpath(path.parent / ref))):
                if candidate in paths and candidate not in selected:
                    selected.add(candidate)
                    pending.append(candidate)
    return sorted(p for p in selected if (root / p).is_file())


def signature(found):
    # Include duplicate occurrences; moving lines does not invalidate a waiver.
    return hashlib.sha256('\n'.join(sorted(command for _, command in found)).encode()).hexdigest()


def waivers(root):
    entries = {}
    for relative in ('runtime/history_reads_waivers.tsv', 'tools/publish/history_reads_waivers.tsv'):
        path = root / relative
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            name, digest, reason = line.split('\t', 2)
            assert name not in entries and re.fullmatch('[0-9a-f]{64}', digest) and reason.strip(), line
            entries[name] = digest
    return entries


def scan(root, paths, inventory=False, allow=True):
    exemptions = waivers(root) if allow else {}
    findings = waived = 0
    for path in paths:
        found = reads(root / path)
        if not found:
            continue
        digest = signature(found)
        exempt = exemptions.get(path.as_posix()) == digest
        if exempt:
            waived += len(found)
        else:
            findings += len(found)
        if inventory:
            print(f'{path}\t{digest}\t{len(found)} reads')
        if inventory or not exempt:
            for line, command in found:
                print(f'{path}:{line}: {"WAIVED" if exempt else "FAIL"}: {command}')
    print(f'history reads: {len(paths)} files, {findings} unwaived, {waived} waived')
    return bool(findings)


def proof():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fixture = root / 'fixture.sh'
        fixture.write_text('git show 13984ab7:host/audio/dsound_host.cpp\n')
        assert scan(root, [Path('fixture.sh')], allow=False) == 1
        # Each fixture must fail the real scanner, not only a parser assertion.
        # These thirteen escaped detection before the patch-form fix.
        missed = (
            'git show REV -- runtime/file.c',
            'git show',
            'git show REV',
            'git log --patch-with-stat HEAD',
            'git log --patch-with-raw HEAD',
            'git log -u HEAD',
            'git log --cc HEAD',
            'git log -c HEAD',
            'git log -L 1,2:runtime/file.c HEAD',
            'git log -U3 HEAD',
            'git log --unified=3 HEAD',
            'git log --binary HEAD',
            'git log --remerge-diff HEAD',
        )
        content = missed + (
            'git show HEAD:runtime/file.c',
            'git show :runtime/file.c',
            'git cat-file -p HEAD:runtime/file.c',
            'git cat-file blob HEAD:runtime/file.c',
            'git cat-file --batch',
            'git cat-file --batch-command',
            'git archive HEAD',
            'git archive --format=tar HEAD runtime',
            'git log -p HEAD',
            'git log --patch HEAD',
            'git log -1p HEAD -- runtime/file.c',
            'git -C repo log --patch=combined HEAD',
            'git --no-pager show HEAD -- runtime/file.c',
            'git log --patch -- runtime/file.c',
        )
        py = root / 'fixture.py'
        for command in content:
            for path, source in ((fixture, command),
                                 (py, 'run(' + repr(command.split()) + ')')):
                path.write_text(source + '\n')
                assert len(reads(path)) == 1, source
                assert scan(root, [path.relative_to(root)], allow=False) == 1, source
        commands = (
            "subprocess.run(['git', '-C', str(root), 'show', rev + ':file'])",
            "subprocess.check_output(['git', 'cat-file', '-p', revision])",
            "run(['git', 'archive', '--format=tar', revision])",
            "git('log', '-p', revision)",
            "run('git log --patch HEAD', shell=True)",
            "script = '''git archive HEAD > source.tar'''",
            '''run('git "show" HEAD -- file', shell=True)''',
            '''run("'git' 'show' HEAD -- file", shell=True)''',
        )
        for command in commands:
            py.write_text(command + '\n')
            assert len(reads(py)) == 1, command
        fixture.write_text('# git show HEAD:file\ngit show-ref --verify refs/heads/main\n'
                           'git log -1 --format=%ct\ngit ls-files\n'
                           'git log -Scup\ngit log -Gcup\ngit log -- -path.c\n')
        assert not reads(fixture)
        py.write_text('"""git show HEAD:file is forbidden"""\n# git archive HEAD\nrun(["git", "status"])\n')
        assert not reads(py)
        # A new nested test and its called helper must both be covered.
        nested = root / 'tools/nested/test_fixture.py'
        nested.parent.mkdir(parents=True)
        nested.write_text("run(['sh', 'helper.sh'])\n")
        helper = nested.parent / 'helper.sh'
        helper.write_text('git cat-file -p HEAD:file\n')
        assert Path('tools/nested/helper.sh') in sources(root)
        assert len(reads(helper)) == 1
        old = signature(reads(helper))
        (root / 'runtime').mkdir()
        (root / 'runtime/history_reads_waivers.tsv').write_text(
            'tools/nested/helper.sh\t' + old + '\tGit guard fixture.\n')
        assert scan(root, [Path('tools/nested/helper.sh')]) == 0
        first = helper.read_text().strip()
        helper.write_text(first + '; ' + first + '\n')
        assert len(reads(helper)) == 2
        assert scan(root, [Path('tools/nested/helper.sh')]) == 1
        helper.write_text(first + '\n')
        helper.write_text(helper.read_text() + 'git archive HEAD\n')
        assert scan(root, [Path('tools/nested/helper.sh')]) == 1
    print('history guard proof: 54/54 content fixtures exit 1 / 1 finding; '
          '8 wrapper forms rejected; safe commands pass; helper and waiver expansion checked')


def main():
    if sys.argv[1:2] == ['--scan']:
        return scan(Path.cwd(), [Path(p) for p in sys.argv[2:]], allow=False)
    proof()
    return scan(ROOT, sources(ROOT), inventory='--inventory' in sys.argv)


if __name__ == '__main__':
    sys.exit(main())
