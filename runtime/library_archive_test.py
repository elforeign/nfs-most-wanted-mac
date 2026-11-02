#!/usr/bin/env python3
"""Release archive proof using real clang/ar, no game inputs.

Good: 10 archives / 382 arm64 members at 14.5; bad: added guest-named object
10/10 rejected, last member at 27.0 10/10 rejected, x86_64 10/10 rejected.
Duplicate member at 27.0: 1/1 rejected; missing object: 10/10 rejected.
CLI: good exit 0; guest object exit 1; member at 27.0 exit 1.
"""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('release_libraries', ROOT / 'media/check_libraries.py')
LIB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIB)


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    return result


def rejected(path, expected, message):
    try:
        LIB.check_archive(path, expected)
    except ValueError as error:
        messages = (message,) if isinstance(message, str) else message
        assert any(m in str(error) for m in messages), str(error)
    else:
        raise AssertionError(f'broken archive accepted: {path.name}')


def main():
    if sys.platform != 'darwin' or not all(shutil.which(x) for x in ('clang', 'ar')):
        print('SKIP: needs the macOS clang/ar toolchain')
        return 3
    with tempfile.TemporaryDirectory(prefix='library-archives-') as folder:
        temp = Path(folder)
        source = temp / 'probe.c'
        source.write_text('int independent_library_probe(void) { return 42; }\n')
        for label, arch, version in (('good', 'arm64', '14.5'), ('high', 'arm64', '27.0'),
                                     ('intel', 'x86_64', '14.5')):
            run('clang', '-arch', arch, '-mmacosx-version-min=' + version,
                '-c', str(source), '-o', str(temp / (label + '.o')))
        inventories = LIB.manifest()
        assert len(inventories) == 10 and sum(sum(v.values()) for v in inventories.values()) == 382
        duplicate_proved = False
        for index, (relative, expected) in enumerate(inventories.items()):
            archive = temp / relative
            archive.parent.mkdir(parents=True, exist_ok=True)
            paths = []
            for i, name in enumerate(expected.elements()):
                obj = temp / 'objects' / str(index) / str(i) / name
                obj.parent.mkdir(parents=True)
                shutil.copyfile(temp / 'good.o', obj)
                paths.append(obj)
            run('ar', 'qc', str(archive), *(str(p) for p in paths))
            pristine = archive.read_bytes()
            assert LIB.check_archive(archive, expected) == len(paths)

            guest = temp / 'speed_55.o'
            shutil.copyfile(temp / 'good.o', guest)
            run('ar', 'q', str(archive), str(guest))
            rejected(archive, expected, 'unexpected=')
            archive.write_bytes(pristine)

            for label, message in (('high', 'minimum macOS 27.0.0'),
                                   ('intel', ('thin arm64', 'not a regular ar archive'))):
                # Objects can differ in size; rebuild the archive rather than patch offsets.
                shutil.copyfile(temp / (label + '.o'), paths[-1])
                archive.unlink()
                run('ar', 'qc', str(archive), *(str(p) for p in paths))
                rejected(archive, expected, message)
            shutil.copyfile(temp / 'good.o', paths[-1])

            if not duplicate_proved and any(n > 1 for n in expected.values()):
                duplicate = next(name for name, n in expected.items() if n > 1)
                # Poison only one occurrence; checking by name after extraction would lose it.
                obj = next(p for p in paths if p.name == duplicate)
                shutil.copyfile(temp / 'high.o', obj)
                archive.unlink()
                run('ar', 'qc', str(archive), *(str(p) for p in paths))
                rejected(archive, expected, 'minimum macOS 27.0.0')
                shutil.copyfile(temp / 'good.o', obj)
                duplicate_proved = True

            archive.unlink()
            if len(paths) > 1:
                run('ar', 'qc', str(archive), *(str(p) for p in paths[:-1]))
            else:
                archive.write_bytes(b'!<arch>\n')
            rejected(archive, expected, 'missing=')
            archive.unlink()
            run('ar', 'qc', str(archive), *(str(p) for p in paths))
        assert duplicate_proved
        result = run(sys.executable, '-I', str(ROOT / 'media/check_libraries.py'), '--root', str(temp))
        assert '10 archives, 382 arm64 members' in result.stdout
        print(result.stdout.strip())
        archive = temp / 'host/audio/build/libhostaudio.a'
        pristine = archive.read_bytes()
        command = [sys.executable, '-I', str(ROOT / 'media/check_libraries.py'), '--root', str(temp)]
        run('ar', 'q', str(archive), str(guest))
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert result.returncode == 1 and 'speed_55.o' in result.stderr, result.stderr
        obj = temp / 'dsound_host.o'
        shutil.copyfile(temp / 'high.o', obj)
        archive.unlink()
        run('ar', 'qc', str(archive), str(obj))
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert result.returncode == 1 and 'minimum macOS 27.0.0' in result.stderr, result.stderr
        archive.write_bytes(pristine)
        print('archive controls: guest 10/10, macOS 27.0 10/10, x86_64 10/10, missing 10/10, duplicate 1/1 rejected')
        print('checker CLI: good exit 0; guest object exit 1; macOS 27.0 exit 1')
    return 0


if __name__ == '__main__':
    sys.exit(main())
