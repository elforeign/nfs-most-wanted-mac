#!/usr/bin/env python3
"""List installed FFmpeg archive members and their compiled sources; reject GPL headers.

Inventory comes from every installed lib*.a, never library_members.tsv. Each member
must match a build object byte for byte, then its compiler-generated .d supplies
the primary source. Stale/unmapped archives or missing dependency data fail closed.
This checks source headers, not all licensing obligations of a static build.
"""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('archive_reader', Path(__file__).with_name('check_libraries.py'))
ARCHIVES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVES)


def gpl_header(source):
    # Only the leading comment: code mentioning GPL is not a license declaration.
    text = source.read_text(errors='strict').lstrip()
    header = text.partition('*/')[0] if text.startswith('/*') else '\n'.join(
        line for line in text.splitlines()[:80] if line.lstrip().startswith(('//', ';', '#')))
    header = re.sub(r'[\s*]+', ' ', header)
    return bool(re.search(r'GNU General Public License|\bGPL(?:-[123]|\s+version\s+[123])', header))


def compiled_sources(libraries, build):
    for archive in libraries:
        # Include subdirectories (aarch64 and generated C objects), with duplicate
        # basenames resolved by contents rather than by extraction/overwriting.
        directory = build / archive.stem
        objects = defaultdict(list)
        for obj in directory.rglob('*.o'):
            objects[obj.name].append(obj)
        used = set()
        for name, payload in ARCHIVES.members(archive.read_bytes()):
            digest = hashlib.sha256(payload).digest()
            matches = [p for p in objects[name] if p not in used and
                       hashlib.sha256(p.read_bytes()).digest() == digest]
            if len(matches) != 1:
                raise ValueError(f'{archive.name}:{name}: expected one matching build object, found {len(matches)}')
            obj = matches[0]
            used.add(obj)
            dep = obj.with_suffix('.d').read_text().replace('\\\n', ' ')
            target, separator, dependencies = dep.partition(':')
            if not separator or shlex.split(target) != [obj.relative_to(build).as_posix()]:
                raise ValueError(f'{obj}: invalid dependency target')
            sources = [build / p for p in shlex.split(dependencies)
                       if Path(p).suffix in ('.c', '.S', '.s', '.asm', '.cpp')]
            if not sources or not sources[0].is_file():
                raise ValueError(f'{obj}: missing primary source in compiler dependencies')
            source = sources[0]
            source.relative_to(build)  # Reject external sources we cannot account for.
            yield archive.name, name, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    libraries = sorted((args.root / 'third_party/ffmpeg/lib').glob('lib*.a'))
    if not libraries:
        print('SKIP: built FFmpeg libraries are absent')
        return 3
    build = args.root / 'third_party/src/ffmpeg-7.1.1'
    bad = []
    count = 0
    try:
        installed = {p.name for p in libraries}
        built = {p.name for p in build.glob('lib*/lib*.a')}
        if installed != built:
            raise ValueError(f'installed/build archive sets differ: missing={sorted(built - installed)}, '
                             f'unbuilt={sorted(installed - built)}')
        # Validate the full mapping before declaring the audit complete.
        for archive, member, source in compiled_sources(libraries, build):
            relative = source.relative_to(build).as_posix()
            print(f'{archive}\t{member}\t{relative}')
            count += 1
            if gpl_header(source):
                bad.append(relative)
    except (OSError, ValueError, UnicodeError) as error:
        print(f'FAIL: FFmpeg source inventory: {error}', file=sys.stderr)
        return 1
    for source in sorted(set(bad)):
        print(f'FAIL: GPL source compiled: {source}', file=sys.stderr)
    print(f'FFmpeg sources: {len(libraries)} archives, {count} compiled sources, {len(bad)} GPL headers')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
