"""Generate the shared host/original coverage contract from actual build inputs.

Hook addresses come from hooks.txt and funcs.h; inline dependencies come from
generated calls and header declarations. Host owners come from selected source
definitions, never a second handwritten hook list. Builds also verify linked
definitions so a source declaration cannot hide a missing compiled body.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WRAPPER = re.compile(r'__attribute__\(\(weak\)\) void (f_[0-9A-F]{8})\(Cpu \*c\) '
                     r'\{ \1_orig\(c\); \}')


def visible(paths):
    return sorted(p for p in paths if not p.name.startswith('.'))


def hooks():
    return {f'f_{int(line.split("#", 1)[0].split()[0], 16):08X}'
            for line in (HERE / 'hooks.txt').read_text(encoding='utf-8').splitlines()
            if line.split('#', 1)[0].strip()}


def host_sources(host):
    if host == 'windows':
        return visible((ROOT / 'runtime/win').glob('*.c'))
    # Include the public/private alternatives declared in the Mac build script.
    script = (ROOT / 'runtime/build.sh').read_text(encoding='utf-8')
    script = '\n'.join(line for line in script.splitlines() if not line.lstrip().startswith('#'))
    names = re.findall(r'(?<![\w/])([\w]+\.(?:c|m|mm|cpp))\b', script)
    return sorted({ROOT / 'runtime' / name for name in names
                   if (ROOT / 'runtime' / name).is_file()})


def source_bodies(host):
    owners = {}
    for path in host_sources(host):
        text = path.read_text(encoding='utf-8')
        # Comments between the argument list and opening brace are common.
        clean = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
        declarations = re.sub(r'^#.*$', '', clean, flags=re.M)
        names = set(re.findall(r'^(?:(?:static|inline|_Noreturn)\s+)*'
                               r'(?:void|int|unsigned|float|double|u?int\d+_t)\s+'
                               r'(\w+)\s*\([^;{}]*\)\s*\{', declarations, re.M))
        # Function-generating macros (e.g. ONE(name) in the headless features).
        for macro in re.findall(r'^#define\s+(\w+)\(name\).*?\bname\(', clean, re.M):
            names.update(re.findall(r'^' + macro + r'\((\w+)\)', clean, re.M))
        joined = clean.replace('\\\n', ' ')
        for macro in re.findall(r'^#define\s+(\w+)\(addr[^)]*\)[^\n]*\bf_##addr\(', joined, re.M):
            names.update('f_' + address for address in
                         re.findall(r'^' + macro + r'\(([0-9A-F]{8})\b', clean, re.M))
        names.update(re.findall(r'^(?:int|uint\d+_t|float|double)\s+\*?\s*(\w+)\s*(?:;|=)',
                                declarations, re.M))
        for name in names:
            owners.setdefault(name, []).append(path.relative_to(ROOT).as_posix())
    return owners


def generate(out):
    out = Path(out)
    header = (out / 'funcs.h').read_text(encoding='utf-8')
    declared = set(re.findall(r'void (f_[0-9A-F]{8})\(Cpu \*c\);', header))
    originals = set(re.findall(r'void (f_[0-9A-F]{8})_orig\(', header))
    expected = hooks()
    if not expected <= declared or originals != expected:
        raise ValueError('hooks.txt/generated header mismatch; regenerate recomp/out')
    runtime = (HERE / 'runtime.h').read_text(encoding='utf-8')
    candidates = set(re.findall(r'^(?:void|int|uint\d+_t|float|double|size_t)\s+(\w+)\([^;{}]*\);',
                                header + '\n' + runtime, re.M))
    candidates = {s for s in candidates if not s.startswith('f_')}
    variables = set(re.findall(r'^extern\s+\w+[ \t*]+(\w+)\s*;', header, re.M))
    used, used_variables, wrappers = set(), set(), set()
    for path in visible(out.glob('*.c')):
        text = path.read_text(encoding='utf-8')
        used.update(re.findall(r'\b(\w+)\s*\(', text))
        used_variables.update(variables & set(re.findall(r'\b\w+\b', text)))
        wrappers.update(WRAPPER.findall(text))
    if wrappers != expected:
        raise ValueError('hooks.txt/generated wrappers mismatch; regenerate recomp/out')
    dependencies = candidates & used
    dependencies.update(used_variables)
    owners = {host: source_bodies(host) for host in ('mac', 'windows')}
    rows = []
    for symbol in sorted(expected | dependencies):
        row = dict(symbol=symbol, kind='hook' if symbol in expected else 'inline')
        for host in owners:
            bodies = owners[host].get(symbol, [])
            if not bodies and symbol not in expected:
                raise ValueError(f'{host}: inline dependency has no host body: {symbol}')
            row[host] = dict(bodies=bodies, resolution='host' if bodies else 'original')
        rows.append(row)
    return rows


def check_definitions(rows, host, defined, originals=False):
    missing = []
    for row in rows:
        symbol = row['symbol']
        target = symbol + '_orig' if row[host]['resolution'] == 'original' else symbol
        if target not in defined or (originals and row['kind'] == 'hook' and symbol not in defined):
            missing.append(target)
    if missing:
        raise ValueError(f'{host}: missing compiled coverage bodies: {", ".join(missing)}')


def write(rows, path):
    with Path(path).open('w', newline='\n', encoding='utf-8') as stream:
        stream.write(json.dumps(rows, indent=2) + '\n')


def image_dependencies(remaining, native_owners):
    """Standalone image has no guest hooks. List every external and its library.

    The emitter is a build-time tool, never a new generated guest dependency.
    Native COM methods are indirect SDK calls; named external symbols come from
    the same COFF audit/explicit library registry as the headless host.
    """
    unknown = sorted(set(remaining) - native_owners.keys())
    if unknown:
        raise ValueError('image: unclassified host dependencies: ' + ', '.join(unknown))
    return [dict(symbol=name, kind='native', windows=dict(
        bodies=[native_owners[name]], resolution='desktop-library')) for name in sorted(remaining)]


def mac_definitions(output):
    # Plain nm reports weak generated wrappers as T too. They must never stand
    # in for a promised strong host implementation.
    defined = set()
    for line in output.splitlines():
        match = re.search(r'\bexternal\s+(_\w+)(?:\s|$)', line)
        if match and '(undefined)' not in line and not re.search(r'\bweak\b', line):
            defined.add(match[1][1:])
    return defined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=HERE / 'out')
    parser.add_argument('--host', choices=('mac', 'windows'), required=True)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    rows = generate(args.out)
    if args.binary:
        if args.host != 'mac':
            parser.error('--binary is for the linked Mac runtime; Windows checks direct COFF objects')
        output = subprocess.check_output(['nm', '-m', '-g', str(args.binary)], text=True)
        defined = mac_definitions(output)
        check_definitions(rows, args.host, defined)
    if args.report:
        write(rows, args.report)
    print(f'coverage: {len(rows)} hooks/inline dependencies; {args.host} checked')


if __name__ == '__main__':
    main()
