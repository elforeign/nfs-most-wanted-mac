"""Compile every non-NULL X360 material source with gen_tables' --stub includes.

No game assets or GPU device needed. The production string arrays and builders
are extracted verbatim; only the bytecode hash calculation is replaced with a
fixture hash, so all technique selectors work without shipping game bytecode.
All shell, technique, stage, input scalar type and shadow sampler branches run.
Identical sources are compiled once. Original unguarded builders: 7 failing
input variants in 3 source families (water VS, aux VS/PS); guarded builders:
31 unique sources, 0 compiler failures across 91 cases. The float-input broken
control has exactly 3 compiler failures.
The broken control removes only the two availability guards and requires those
same compiler errors. When real generated includes exist, emitted bytes must
also match the unguarded builders for every case.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
KINDS = ('grass', 'water', 'aux', 'glass', 'trees', 'bone')
GUARD = '    if (!X360_SHADER_BODIES) return NULL;\n'
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|[{};]', re.S)


def function(src, name):
    match = re.search(r'^[^\n]*\b' + name + r'\([^\n]*\) \{', src, re.M)
    assert match, name
    start = src.index('{', match.start())
    depth = 0
    for token in TOKENS.finditer(src, start):
        if token[0] == '{':
            depth += 1
        elif token[0] == '}':
            depth -= 1
            if depth == 0:
                return src[match.start():token.end()] + '\n'
    raise AssertionError(name)


def arrays(src):
    result = []
    used = set()
    for match in re.finditer(r'^static const char (\w+)\[\]\s*=', src, re.M):
        end = next(t.end() for t in TOKENS.finditer(src, match.end()) if t[0] == ';')
        text = src[match.start():end] + '\n'
        if match[1].split('_')[0] in KINDS:
            result.append(text)
            used.update(re.findall(r'\bX360B_\w+', text))
    # Derive coverage independently from every body macro used by materials.m.
    assert used == set(re.findall(r'\bX360B_\w+', src)), 'uncovered shader body'
    return ''.join(result)


def run(args, **kwargs):
    p = subprocess.run(list(map(str, args)), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, **kwargs)
    if p.returncode:
        raise AssertionError(f'{args[0]} exited {p.returncode}:\n{p.stdout[-4000:]}')
    return p.stdout


def harness(src):
    parts = ['#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n',
             '#include "materials.h"\n#include "../host/shaders/sm_coverage.h"\n',
             '#include "x360_shader_lits.inc"\n#include "x360_shader_body.inc"\n',
             'static uint64_t fixture_hash;\n', arrays(src),
             function(src, 'mat_grass_shadow_slot')]
    for kind in KINDS:
        body = function(src, f'mat_{kind}_shader')
        if kind in ('glass', 'trees', 'bone'):
            body, count = re.subn(
                r'    uint64_t h = 1469598103934665603ull;.*?(?=    char \*result)',
                '    uint64_t h = fixture_hash;\n', body, flags=re.S)
            assert count == 1, kind
        parts.append(body)
    parts.append(r'''
static void save(const char *name, char *s) {
    if (!s) { printf("%s NULL\n", name); return; }
    FILE *f = fopen(name, "wb");
    if (!f || fwrite(s, 1, strlen(s), f) != strlen(s) || fclose(f)) exit(2);
    free(s);
    printf("%s SOURCE\n", name);
}
int main(void) {
    SmResult pc = {0};
    SmConstant constant = {0}; pc.constants = &constant;
    const int usages[] = {SM_USAGE_POSITION, SM_USAGE_NORMAL, SM_USAGE_COLOR,
        SM_USAGE_TEXCOORD, SM_USAGE_TANGENT, SM_USAGE_BLENDWEIGHT, SM_USAGE_BLENDINDICES};
    pc.n_inputs = sizeof usages / sizeof *usages;
    for (int i = 0; i < pc.n_inputs; ++i) {
        pc.inputs[i].reg = i; pc.inputs[i].usage = usages[i];
    }
    pc.n_constants = 1; pc.constants[0].set = 3; pc.constants[0].reg = 1;
    strcpy(pc.constants[0].name, "shadow_sampler");
    pc.n_samplers = 1; pc.samplers[0].reg = 1;
    for (int stage = 0; stage < 2; ++stage)
    for (int type = 0; type < (stage == SM_VERTEX ? 3 : 1); ++type)
    for (int shadow = 0; shadow < (stage == SM_PIXEL ? 2 : 1); ++shadow) {
        uint16_t si = type == 1 ? 127 : 0, ui = type == 2 ? 127 : 0;
        pc.samplers[0].shadow = shadow;
        char name[128];
''')
    # Explicit selectors exercise every accepted source branch, without assets.
    selectors = {
        'glass': (('0xEAA7AABC9BAF0607', '0x3B7041897590C529', '0x74E7D294D4B2ECD6'),
                  ('0xA8D19FEBB9579A2B', '0x0919FCE65F8531A1', '0xF77FCE200B15449C',
                   '0x40064267BE4B6733', '0xD4B6AB8CE477F25A', '0x10B22A6361C33088')),
        'trees': (('0x96681B7C39F397A7',), ('0x460871E26A412F77',)),
        'bone': (('0x112427A3B5E74D1E',), ('0xFD2B54560A2564FF',)),
    }
    for kind in KINDS:
        if kind in selectors:
            for stage, hashes in enumerate(selectors[kind]):
                for i, h in enumerate(hashes):
                    parts.append(f'if (stage == {stage}) {{ fixture_hash = {h}ULL;\n'
                                 f'snprintf(name,sizeof name,"{kind}_%d_{i}_%d_%d.metal",stage,type,shadow);\n'
                                 f'save(name,mat_{kind}_shader(NULL,0,&pc,stage,si,ui)); }}\n')
        elif kind == 'grass':
            parts.append('for (int shell=0;shell<10;++shell) {\n'
                         'snprintf(name,sizeof name,"grass_%d_%d_%d_%d.metal",stage,shell,type,shadow);\n'
                         'save(name,mat_grass_shader(&pc,stage,shell,si,ui)); }\n')
        else:
            parts.append(f'snprintf(name,sizeof name,"{kind}_%d_0_%d_%d.metal",stage,type,shadow);\n'
                         f'save(name,mat_{kind}_shader(&pc,stage,si,ui));\n')
    parts.append('} return 0; }\n')
    return ''.join(parts)


def emit(src, inc, work):
    work.mkdir()
    path = work / 'emit.c'
    path.write_text(harness(src))
    exe = work / 'emit'
    run(['clang', '-std=c11', '-I', inc, '-I', RT, path, '-o', exe])
    manifest = run([exe], cwd=work)
    cases = dict(line.split() for line in manifest.splitlines())
    return {name: (work / name).read_bytes() if status == 'SOURCE' else None
            for name, status in cases.items()}


def compile_sources(cases, work):
    unique = {}
    for name, data in cases.items():
        if data is not None:
            unique.setdefault(data, name)
    failures = {}
    for data, name in unique.items():
        path = work / name
        path.write_bytes(data)
        p = subprocess.run(['xcrun', '-sdk', 'macosx', 'metal', '-c', str(path),
                            '-fmodules-cache-path=' + str(work.parent / 'modules'),
                            '-o', str(work / 'source.air')], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode:
            failures[name] = p.stdout
    print(f'{work.name}: {len(cases)} cases, {len(unique)} unique non-NULL sources, '
          f'{len(failures)} compiler failures')
    return failures


def main():
    if not shutil.which('xcrun') or subprocess.run(
            ['xcrun', '-sdk', 'macosx', 'metal', '--version'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        print('SKIP: xcrun metal is missing')
        return 3
    src = (RT / 'materials.m').read_text()
    with tempfile.TemporaryDirectory(prefix='materials-stub-') as temp:
        work = Path(temp)
        for generator, output in (('x360_shader_lits.py', 'x360_shader_lits.inc'),
                                  ('x360_shader_body.py', 'x360_shader_body.inc')):
            run([sys.executable, '-I', ROOT / 'tools_snapshot' / generator,
                 '--stub', '--out', work / output])
        actual = emit(src, work, work / 'actual')
        failures = compile_sources(actual, work / 'actual')
        if failures:
            for name, log in failures.items():
                print(f'{name}:\n{log[-1800:]}')
            return 1
        for name, data in actual.items():
            assert (data is None) == name.startswith(('water_', 'aux_')), name
        broken_src = src.replace(GUARD, '')
        assert broken_src != src, 'availability guards missing'
        broken = emit(broken_src, work, work / 'broken')
        assert all(data is not None for data in broken.values())
        failed = compile_sources({n: d for n, d in broken.items()
                                  if n.endswith('_0_0.metal')}, work / 'broken')
        assert set(failed) == {'water_0_0_0_0.metal', 'aux_0_0_0_0.metal',
                               'aux_1_0_0_0.metal'}, failed.keys()
        for name, regs in (('water_0_0_0_0.metal', ('r10', 'r11')),
                           ('aux_0_0_0_0.metal', ('r1',)),
                           ('aux_1_0_0_0.metal', ('r13', 'r10'))):
            assert all(f"undeclared identifier '{reg}'" in failed[name] for reg in regs), failed[name]
        real = RT / 'gen'
        if (real / 'x360_shader_body.inc').exists() and re.search(
                r'#define X360_SHADER_BODIES\s+1', (real / 'x360_shader_body.inc').read_text()):
            a = emit(src, real, work / 'real')
            b = emit(broken_src, real, work / 'real-original')
            assert a == b, 'real generated material sources changed'
            print(f'real includes: {len(a)} cases byte-identical')
        print('PASS: stub material compilation and three-source broken control')
    return 0


if __name__ == '__main__':
    sys.exit(main())
