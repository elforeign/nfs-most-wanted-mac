#!/usr/bin/env python3
"""Keep build source and archive lists at one file per physical line.

Original scripts: 30 failing lines (runtime/build.sh: 7); reformatted: 0.
Pinned original runtime fragment: 1 failing line (8 files); split form: 0.
Option-value controls: 26 false failing lines before the fix; 0 after.
Real two-source controls: 26 failing lines before and after the fix.
The offline suite discovers this driver automatically.
"""
import hashlib
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ('runtime', 'host', 'media', 'recomp')
START = re.compile(r'^\s*(?:(?:clang(?:\+\+)?|gcc|g\+\+|cc|c\+\+|\$[A-Za-z_]\w*)\s|[A-Za-z_]\w*=)')
FILE = re.compile(r'[^\s=]+\.(?:c|m|mm|cpp|a)$')
# These driver options consume the next token when their value is separate.
# Attached values are excluded along with every other option token below.
VALUE_OPTIONS = frozenset('''
    -I -F -L -B -D -U -include -include-pch -imacros -isystem -iquote
    -idirafter -iframework -isysroot --sysroot -o -MF -MT -MQ -MJ -x
    -target --target -arch -stdlib -resource-dir -gcc-toolchain --gcc-toolchain
    -Xclang -Xlinker -Xassembler -Xpreprocessor -framework -weak_framework
'''.split())
ORIGINAL = 'boot.c cash_cheat.c underglow_ui.c underglow.c underglow_store.c underglow_photometry.c car85.c contact_mass.c'
ORIGINAL_SHA256 = 'ee0d96e972890e7029b35f385f1595b9c310d9bf3c0dc8ae882a0cf31f59f863'


def words(line):
    # Quoted list assignments may span physical lines. Close the local slice
    # for tokenization only; completeness is tracked on the accumulated command.
    for ending in ('', '"', "'"):
        try:
            return shlex.split(line.rstrip().removesuffix('\\') + ending, comments=True)
        except ValueError:
            pass
    raise AssertionError('cannot tokenize source-list line')


def findings(text):
    pending = ''
    option_value = False
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        if not pending and not START.match(line):
            continue
        pending += line + '\n'
        files = []
        for word in words(line):
            if option_value:
                option_value = False
                continue
            if word.startswith('-'):
                option_value = word in VALUE_OPTIONS
                continue
            # Shell assignments contain a word-split list, unlike -D macros.
            if re.match(r'^[A-Za-z_]\w*=', word):
                candidates = word.split('=', 1)[1].split()
            else:
                candidates = [word]
            files.extend(w for w in candidates if FILE.fullmatch(w.rstrip(';)')))
        if len(files) >= 2:
            result.append((number, len(files)))
        try:
            shlex.split(pending, comments=True)
            complete = not line.rstrip().endswith('\\')
        except ValueError:
            complete = False
        if complete:
            pending = ''
            option_value = False
    return result


def scripts():
    return sorted(p for folder in ROOTS for p in (ROOT / folder).rglob('*build.sh')
                  if not any(part.startswith('.') or part in ('build', 'out', 'dist', 'node_modules')
                             for part in p.relative_to(ROOT).parts))


def main():
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == ORIGINAL_SHA256
    assert findings('clang ' + ORIGINAL + '\n') == [(1, 8)], 'original long line escaped check'
    split = 'clang \\\n' + ' \\\n'.join(ORIGINAL.split()) + '\n'
    assert findings(split) == [], 'split original rejected'
    # Each supported suffix, quoted archives, source-valued assignments and
    # compiler macros exercise the parser without depending on current scripts.
    for suffix in ('.c', '.m', '.mm', '.cpp', '.a'):
        assert findings(f'clang one{suffix} two{suffix}\n') == [(1, 2)]
        assert findings(f'clang one{suffix} \\\n    two{suffix}\n') == []
    assert findings('LIBS="$F/one.a $F/two.a"\n') == [(1, 2)]
    assert findings('SRC="\n one.c two.c\n"\n') == [(2, 2)]
    assert findings('clang -DSOURCE=\\"other.cpp\\" main.cpp\n') == []
    for option in ('-I', '-include', '-o', '-MF', '-L', '-x', '-isystem',
                   '-iquote', '-imacros', '-F', '-B', '-MT', '-MQ'):
        for separator in ('', ' '):
            prefix = f'clang {option}{separator}vendor/generated.c '
            assert findings(prefix + 'main.c\n') == [], (option, separator)
            assert findings(prefix + 'one.c two.c\n') == [(1, 2)]
        assert findings(f'clang {option} \\\n    vendor/generated.c main.c\n') == []
    assert findings('clang --sysroot=vendor/generated.c main.c\n') == []
    assert findings('clang -I vendor/generated.c \\\n    one.c two.c\n') == [(2, 2)]
    assert findings('clang -I vendor/generated.c main.c\nclang one.c two.c\n') == [(2, 2)]
    assert findings('# clang a.c b.c\nsed s/a.c/b.c/ in.c > out.c\n') == []
    paths = [Path(arg) for arg in sys.argv[1:]] or scripts()
    assert paths, 'no build scripts found'
    failures = [(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p), line, count)
                for p in paths for line, count in findings(p.read_text())]
    for path, line, count in failures:
        print(f'{path}:{line}: {count} source/archive files on one line')
    print(f'build source lines: {len(failures)} failing lines across {len(paths)} scripts; '
          'pinned original: 1 rejected line / 8 files')
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
