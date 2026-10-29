"""Built-in private overlay resolver and packaging (no game/assets needed).
Plan: compile the production resolver in private/public fixtures; exercise real
bundle.sh with harmless tool stubs; reject a resolver mutant that ignores off.
Proof: production 17/17 assertions; ignores-off mutant 2/14 failures (off cases).
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNTIME = Path(__file__).resolve().parent
if not (RUNTIME / "later_textures_private.h").is_file():
    print("SKIP: optional overlay fixtures are absent")
    sys.exit(3)


def resolver(source, root):
    helper = source[source.index('static const char *later_root('):source.index('static void hd_scan(')]
    code = '''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <mach-o/dyld.h>
''' + helper + '\nint main(void) { char p[4096], canonical[4096]; const char *r = later_root(p, sizeof p); puts(r && realpath(r, canonical) ? canonical : "none"); }\n'
    c = root / 'fixture.c'
    c.write_text(code)
    checkout = root / 'checkout'
    boot = checkout / 'runtime/boot'
    app = root / 'Private.app/Contents/MacOS/NFSMW'
    public = root / 'Public.app/Contents/MacOS/NFSMW'
    for binary in (boot, app, public):
        binary.parent.mkdir(parents=True)
        subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(binary)] +
                       (['-DNFS_PUBLIC=1'] if binary == public else []), check=True, capture_output=True)
    env = dict(os.environ)
    env.pop('NFS_LATER_TEXTURES', None)
    checks = []

    def check(binary, setting, expected):
        e = dict(env)
        if setting is not None:
            e['NFS_LATER_TEXTURES'] = setting
        out = subprocess.run([str(binary)], cwd='/', env=e, check=True, text=True, capture_output=True).stdout.strip()
        checks.append(out == (str(expected.resolve()) if expected else 'none'))

    default = checkout / 'recomp/out/later_textures'
    default.mkdir(parents=True)
    custom = root / 'custom'
    custom.mkdir()
    plain = root / 'file'
    plain.touch()
    for setting, expected in ((None, default), ('', default), (str(custom), custom), ('off', None), ('0', None),
                              (str(root / 'missing'), None), (str(plain), None)):
        check(boot, setting, expected)
    default.rmdir()
    check(boot, None, None)
    bundled = app.parent.parent / 'Resources/later_textures'
    bundled.mkdir(parents=True)
    for setting, expected in ((None, bundled), ('off', None), ('0', None)):
        check(app, setting, expected)
    bundled.rmdir()
    check(app, None, None)
    # Public excludes even an explicit existing path.
    check(public, None, None)
    check(public, str(custom), None)
    return checks


def packaging(root):
    runtime = root / 'runtime'
    runtime.mkdir(parents=True)
    script = runtime / 'bundle.sh'
    script.write_bytes((RUNTIME / 'bundle.sh').read_bytes())
    boot = runtime / 'boot'
    boot.write_text('#!/bin/sh\nexit 0\n')
    boot.chmod(0o755)
    (runtime / 'app_icon.m').touch()
    bin_dir = root / 'bin'
    bin_dir.mkdir()
    # No Cocoa app runs: clang emits a harmless icon-generator stub here.
    stubs = {
        'otool': "printf 'LC_BUILD_VERSION\\n minos 14.0\\n'\n",
        'clang': '''while [ "$1" != -o ]; do shift; done
shift
printf '#!/bin/sh\\ntouch "$1"\\n' > "$1"
chmod +x "$1"
''',
        'iconutil': 'while [ "$1" != -o ]; do shift; done\nshift\ntouch "$1"\n',
        'sips': 'exit 0\n', 'plutil': 'exit 0\n', 'codesign': 'exit 0\n',
    }
    for name, body in stubs.items():
        p = bin_dir / name
        p.write_text('#!/bin/sh\n' + body)
        p.chmod(0o755)
    folder = root / 'recomp/out/later_textures'
    folder.mkdir(parents=True)
    (folder / 'sentinel.dds').write_bytes(b'fixture only')
    checks = []
    for public, present in (('0', True), ('1', True), ('0', False)):
        if not present:
            (folder / 'sentinel.dds').unlink()
            folder.rmdir()
        e = dict(os.environ, NFS_PUBLIC=public, PATH=str(bin_dir) + ':' + os.environ['PATH'])
        # Reuse the destination: public must remove a previously bundled private set.
        subprocess.run(['/bin/sh', str(script), str(root / 'output'), '--name', 'Fixture'], env=e,
                       check=True, capture_output=True)
        copied = root / 'output/Fixture.app/Contents/Resources/later_textures'
        checks.append(copied.exists() == (public == '0' and present))
        if copied.exists():
            assert (copied / 'sentinel.dds').read_bytes() == b'fixture only'
    return checks


def main():
    source = (RUNTIME / 'x360_textures.m').read_text()
    with tempfile.TemporaryDirectory(prefix='latertex-default-') as tmp:
        root = Path(tmp)
        good = root / 'good'
        good.mkdir()
        checks = resolver(source, good)
        checks += packaging(root / 'package')
        assert all(checks), checks
        bad = root / 'bad'
        bad.mkdir()
        mutant = source.replace('if (e && (!strcmp(e, "off") || !strcmp(e, "0"))) return NULL;',
                                'if (e && !strcmp(e, "off")) e = NULL;\n    if (e && !strcmp(e, "0")) return NULL;', 1)
        broken = resolver(mutant, bad)
        failures = broken.count(False)
        assert failures == 2, broken
        print(f'PASS: production {sum(checks)}/{len(checks)}; ignores-off mutant {failures}/{len(broken)} failures (2 off cases)')


if __name__ == '__main__':
    main()
