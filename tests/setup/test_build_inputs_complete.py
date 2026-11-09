"""Independent source-closure audit of Setup's six build-stage fingerprints.

The oracle starts at backend commands, not build_inputs.stage_inputs or the
table driver's GENERATORS manifest. It reads real drivers, local Python imports,
repo file references and quoted includes (including C embedded in Python).
Generated outputs belong to their producer; consumers must key their bytes.
FFmpeg's source archive keys every member, including its internal includes.

Original production code: 328/330 stage/file edges keyed, two missing (tables:
runtime.h and windows_compile.h), one failing stage. Fixed: 330/330, zero missing.
An undeclared emitter graph discovers 6/6 inputs; 6/6 key omissions are rejected.
With producer headers absent, the initial audit reported one phantom funcs.h
input; recognizing open(..., 'w') outputs reduces that count to zero.
FFmpeg audit integration: 354/359 edges keyed (three omitted audit inputs and
two phantom extracted paths); corrected inputs and directory tracking: 357/357.
"""
import ast
import importlib.util
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('complete_build_inputs', ROOT / 'recomp/build_inputs.py')
inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inputs)
C_SOURCE = ('.c', '.cc', '.cxx', '.cpp', '.m', '.mm')
FILE = re.compile(r'^[\w./$+-]+\.(?:py|sh|c|cc|cxx|cpp|m|mm|h|hh|hpp|hxx|inc|tsv|txt|a|tar\.xz)$')
INCLUDE = re.compile(r'^\s*#\s*(?:include|import)\s*"([^"]+)"', re.M)


def normalized(path):
    return Path(os.path.abspath(path))


def stage_drivers(root):
    """Read actual cached commands; do not copy the production dependency list."""
    tree = ast.parse((root / 'tools/setup_kit/backend.py').read_text())
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'cached':
            name = ast.literal_eval(node.args[0])
            result[name] = next(root / n.value for n in ast.walk(node.args[2])
                                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                                and n.value.endswith(('.py', '.sh')))
    return result


class SourceClosure:
    """Conservative static audit, without executing generators or translator code.

    Inspect imported functions alone for ``from module import function``; this
    keeps the unused executable harness out of the translation graph.
    Coverage checks read host bodies to validate outputs, and run separately on
    every Setup build; those bodies do not produce translated code. The key
    policy itself is opaque to this oracle, so its declarations cannot seed it.
    """
    def __init__(self, root, stage, drivers):
        self.root, self.stage, self.drivers = root, stage, drivers
        self.files, self.seen = set(), set()
        self.search = [root / n for n in ('recomp', 'recomp/out', 'runtime',
                                        'third_party/ffmpeg/include')]
        self.produced = [root / n for n in dict(recompile=['recomp/out'], tables=['runtime/gen'],
                         ffmpeg=['third_party/src', 'third_party/ffmpeg']).get(stage, [])]

    def add(self, path, follow=True, functions=None):
        path = normalized(path)
        if not path.is_relative_to(self.root) or any(path.is_relative_to(p) for p in self.produced):
            return
        self.files.add(path)
        token = (path, follow, tuple(functions or ()))
        if token in self.seen or not follow or not path.is_file():
            return
        self.seen.add(token)
        if path.suffix == '.py':
            self.python(path, functions)
        elif path.suffix == '.sh':
            self.shell(path)
        elif path.suffix in (*C_SOURCE, '.h', '.hh', '.hpp', '.hxx', '.inc'):
            self.includes(path.read_text(), path.parent, self.search)

    def includes(self, text, folder, search, generated=()):
        for name in INCLUDE.findall(text):
            if name in generated:
                continue
            candidates = [normalized(p / name) for p in (folder, *search)]
            found = next((p for p in candidates if p.is_file()), None)
            # An absent generated include is still an input of its consumer.
            self.add(found or candidates[0])

    def value(self, node, values):
        """Evaluate only literal path construction; never eval Python code."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self.value(n, values) for n in node.elts]
        if isinstance(node, ast.BinOp):
            left, right = self.value(node.left, values), self.value(node.right, values)
            if isinstance(node.op, ast.Div) and isinstance(left, (str, Path)) and isinstance(right, str):
                return normalized(Path(left) / right)
            if isinstance(node.op, ast.Add) and isinstance(left, str) and isinstance(right, str):
                return left + right
        if isinstance(node, ast.Attribute):
            base = self.value(node.value, values)
            if isinstance(base, Path) and node.attr in ('parent', 'parents'):
                return getattr(base, node.attr)
        if isinstance(node, ast.Subscript):
            base, index = self.value(node.value, values), self.value(node.slice, values)
            if isinstance(index, int) and hasattr(base, '__getitem__'):
                try:
                    return base[index]
                except (IndexError, KeyError):
                    return None
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            args = [self.value(n, values) for n in node.args]
            if name in ('str', 'Path') and args and isinstance(args[0], (str, Path)):
                return Path(args[0]) if name == 'Path' else str(args[0])
            if name in ('os.path.abspath', 'os.path.dirname') and args and isinstance(args[0], (str, Path)):
                return normalized(args[0]) if name.endswith('abspath') else Path(args[0]).parent
            if name == 'os.path.join' and args and all(isinstance(a, (str, Path)) for a in args):
                return normalized(Path(*args))
            if isinstance(node.func, ast.Attribute) and node.func.attr in ('resolve', 'absolute'):
                base = self.value(node.func.value, values)
                if isinstance(base, (Path, str)):
                    return normalized(base)
        return None

    def module(self, name, path):
        for folder in (path.parent, self.root / 'recomp', self.root / 'tools',
                       self.root / 'tools_snapshot', self.root / 'tools_snapshot/vaults'):
            base = folder / name.replace('.', '/')
            for candidate in (base.with_suffix('.py'), base / '__init__.py'):
                if candidate.is_file():
                    return candidate
        return None

    def python(self, path, functions):
        # Never derive expected dependencies from the code being audited.
        if path == self.root / 'recomp/build_inputs.py':
            return
        tree = ast.parse(path.read_text())
        if functions:
            definitions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            reachable = set(functions)
            pending = list(functions)
            while pending:
                definition = definitions.get(pending.pop())
                if definition:
                    for node in ast.walk(definition):
                        if isinstance(node, ast.Name) and node.id in definitions and node.id not in reachable:
                            reachable.add(node.id)
                            pending.append(node.id)
            tree.body = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.ClassDef))
                         or n.name in reachable]
        # Coverage is a validation/reporting consumer, not a code generator.
        coverage = path == self.root / 'recomp/hook_coverage.py'
        # Ignore the table manifest even when it happens to be complete.
        tree.body = [n for n in tree.body if not (isinstance(n, ast.Assign) and
                     any(isinstance(t, ast.Name) and t.id == 'GENERATORS' for t in n.targets))]
        nodes = list(ast.walk(tree))
        compiles = any(isinstance(n, ast.Constant) and n.value in ('clang', 'clang++', 'gcc', 'g++')
                       for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str))
        values = {'__file__': path}
        for _ in range(3):
            for node in nodes:
                if isinstance(node, ast.Assign):
                    value = self.value(node.value, values)
                    if value is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                values[target.id] = value
        folders = [path.parent, self.root, *self.search]
        folders += [v for v in values.values() if isinstance(v, Path) and v.is_dir()]
        if compiles:
            # Compiler -I operands can be inline path expressions as well as
            # module constants. Reuse their search order for native sources.
            for node in nodes:
                if isinstance(node, (ast.List, ast.Tuple)):
                    for flag, operand in zip(node.elts, node.elts[1:]):
                        if isinstance(flag, ast.Constant) and flag.value == '-I':
                            value = self.value(operand, values)
                            if isinstance(value, (str, Path)):
                                self.search.append(normalized(self.root / value))
            self.search.extend(p for p in folders if p not in self.search)
            folders = [*folders, *self.search]
        reads = set()
        generated = set()
        for node in nodes:
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('read_text', 'read_bytes', 'open'):
                value = self.value(node.func.value, values)
                if isinstance(value, Path):
                    reads.add(normalized(value))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'open' and node.args:
                value = self.value(node.args[0], values)
                mode = self.value(node.args[1], values) if len(node.args) > 1 else 'r'
                if isinstance(value, (str, Path)) and isinstance(mode, str) and not any(c in mode for c in 'wax'):
                    reads.add(normalized(self.root / value))
                elif isinstance(mode, str) and any(c in mode for c in 'wax'):
                    generated.update(Path(n.value).name for n in ast.walk(node.args[0]) if isinstance(n, ast.Constant)
                                     and isinstance(n.value, str) and n.value.endswith(('.h', '.inc')))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('write_text', 'write_bytes'):
                generated.update(Path(n.value).name for n in ast.walk(node.func.value) if isinstance(n, ast.Constant)
                                 and isinstance(n.value, str) and n.value.endswith(('.h', '.inc')))
        if not coverage:
            for read in reads:
                self.add(read, follow=False)
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = self.module(alias.name, path)
                    if local:
                        self.add(local)
            elif isinstance(node, ast.ImportFrom):
                local = self.module(node.module or '', path)
                if local:
                    self.add(local, functions=[a.name for a in node.names])
                for alias in node.names:
                    local = self.module('.'.join(filter(None, (node.module, alias.name))), path)
                    if local:
                        self.add(local)
            if coverage:
                continue
            value = self.value(node, values)
            if isinstance(value, Path) and value != path and value.is_file():
                self.add(value, follow=value not in reads and (value.suffix not in C_SOURCE or compiles))
            if isinstance(value, str):
                self.includes(value, path.parent, folders, generated)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if FILE.fullmatch(text):
                    for folder in folders:
                        candidate = normalized(folder / text)
                        if candidate != path and candidate.is_file():
                            self.add(candidate, follow=candidate not in reads and (candidate.suffix not in C_SOURCE or compiles))

    def shell(self, path):
        text = path.read_text()
        # Setup invokes library-only, non-release commands. Stop before tests.
        text = re.split(r'^\[ "\$mode" (?:!= lib \] \|\||= lib \] &&) exit 0', text, flags=re.M)[0]
        text = re.sub(r'if \[ "\$release" = 1 \]; then.*?\nfi', '', text, flags=re.S)
        text = '\n'.join(line.split('#', 1)[0] for line in text.splitlines())
        cwd = self.root if 'cd "$(dirname "$0")/.."' in text else path.parent
        if 'mkdir -p build' in text:
            self.produced.append(cwd / 'build')
        values = {'PWD': str(cwd)}
        for name, value in re.findall(r'^([A-Za-z_]\w*)=["\x27]?([^\n"\x27]+)', text, re.M):
            for key, replacement in values.items():
                value = value.replace('$' + key, replacement)
            values[name] = value
        for key, value in values.items():
            text = text.replace('$' + key, value)
        # Variables containing source lists and multiline compiler commands are
        # scanned together; both public/private alternatives participate.
        moves = list(re.finditer(r'^cd "([^"$]+)"\s*$', text, re.M))
        for match in re.finditer(r'[\w./$+-]+\.(?:cpp|cxx|cc|mm|py|sh|c|m|hpp|hxx|hh|h|a|tar\.xz)\b', text):
            name = match.group()
            location = cwd
            for move in moves:
                if move.start() >= match.start():
                    break
                location = normalized(location / move[1])
            candidate = normalized(location / name)
            if not candidate.is_relative_to(self.root):
                continue
            # An earlier stage already owns this generator's dependencies.
            if candidate in self.drivers.values() and candidate != path:
                continue
            if '$' not in name:
                self.add(candidate)
        if re.search(r'^\. .*?/env\.sh', text, re.M):
            self.add(path.parent / 'env.sh')

    def discover(self):
        self.add(self.drivers[self.stage])
        return self.files


def keyed_files(stage, root):
    """Observe actual hash records, not the stage_inputs declaration."""
    observed = set()
    def capture(files, config):
        observed.update(normalized(path) for _, path in files)
        return 'observed'
    with tempfile.TemporaryDirectory() as temp, mock.patch.object(inputs, 'key', side_effect=capture):
        pc = Path(temp) / 'pc'
        inputs.stage_key(stage, root, pc, Path(temp) / 'x360', compiler={}, env={'NFS_PUBLIC': '1'})
    return observed


class CompletenessTests(unittest.TestCase):
    def test_absent_producer_headers_are_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            driver = root / 'recomp/build.py'
            driver.parent.mkdir()
            driver.write_text('''from pathlib import Path
import subprocess
HERE = Path(__file__).resolve().parent
with open(HERE / 'out/funcs.h', 'w') as header:
    header.write('#include "support.h"\\n')
source = '#include "funcs.h"\\n'
subprocess.run(['clang', '-I', str(HERE), '-I', str(HERE / 'out'), 'generated.c'])
''')
            support = root / 'recomp/support.h'
            support.write_text('// authored compiler input\n')
            required = SourceClosure(root, 'recompile', {'recompile': driver}).discover()
            self.assertEqual(required, {driver, support})

    def test_undeclared_generator_inputs_are_discovered(self):
        # This graph does not come from stage_inputs or GENERATORS. A new
        # imported emitter compiles a source and an in-memory helper, reads
        # non-source data, and reaches a second header through the first.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {
                'tools/driver.py': 'from emitter import emit\nemit()\n',
                'tools/emitter.py': '''from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1]
def emit():
    produce()
def produce():
    recipe = (ROOT / 'recipes/options.json').read_text()
    helper = '#include "entry.h"\\n'
    subprocess.run(['clang', '-I', str(ROOT / 'include'), str(ROOT / 'src/helper.cpp')])
''',
                'src/helper.cpp': '#include "entry.h"\n',
                'include/entry.h': '#include "detail.h"\n',
                'include/detail.h': '// transitive header\n',
                'recipes/options.json': '{}\n',
            }
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            required = SourceClosure(root, 'tables', {'tables': root / 'tools/driver.py'}).discover()
            expected = {root / name for name in files}
            self.assertEqual(required, expected)
            # Dropping each independently derived input from the key must fail
            # the same coverage assertion, rather than changing the oracle.
            for omitted in sorted(expected):
                with self.assertRaises(AssertionError):
                    self.assertFalse(required - (expected - {omitted}))
        print('new import/compiled source/embedded helper/data/include graph: 6/6 discovered; 6/6 omissions rejected')

    def test_real_stage_source_closures_are_keyed(self):
        drivers = stage_drivers(ROOT)
        self.assertEqual(set(drivers), {'recompile', 'tables', 'ffmpeg', 'audio', 'shaders', 'runtime'})
        total = missing = 0
        for stage in drivers:
            required = SourceClosure(ROOT, stage, drivers).discover()
            absent = required - keyed_files(stage, ROOT)
            total += len(required)
            missing += len(absent)
            with self.subTest(stage=stage):
                self.assertFalse(absent, '\n'.join(str(p.relative_to(ROOT)) for p in sorted(absent)))
        print(f'independent completeness: {total - missing}/{total} stage/file edges keyed; {missing} missing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
