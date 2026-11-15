"""Production ripple patch + height texture: roof/open/gap/above/transformed/authored.

Fixed: 0/24 channel failures; deliberately reversed GPU comparison: 15/24 FAIL.
Film dry/wet: fixed 0/48; omitted film patch 18/48 FAIL.
No installed assets needed for the six synthetic positions, sampled dry/wet. Installed shader
fixtures additionally compile all translated worldreflect vertex/pixel paths.
Use run_tests for a Metal device in the trusted test sandbox.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'


def function(source, name):
    match = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^\n]*\) \{', source, re.M)
    assert match, name
    start = source.index('{', match.start())
    depth = 0
    for token in re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|[{}]', source[start:], re.S):
        if token[0] == '{':
            depth += 1
        elif token[0] == '}':
            depth -= 1
            if depth == 0:
                return source[match.start():start + token.end()]
    raise AssertionError(name)


VERTEX = '''#include <metal_stdlib>
using namespace metal;
struct SmVSStageOut {
    float4 pos [[position]];
    float4 texcoord2 [[user(texcoord2)]];
    float4 texcoord3 [[user(texcoord3)]];
    float4 texcoord4 [[user(texcoord4)]];
};
struct SmVSIn {float4 v[1];};
struct SmVSOut {float4 tex[3];};
vertex SmVSStageOut sm_main(constant float4 &point [[buffer(0)]], uint id [[vertex_id]]) {
    float2 corners[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};
    SmVSIn si;si.v[0]=point;SmVSOut so={};SmVSStageOut r={};r.pos=float4(corners[id],0,1);
    r.texcoord2 = so.tex[2];
    r.texcoord4 = float4(0,0,1,0);
    return r;
}
'''

PIXEL = '''#include <metal_stdlib>
using namespace metal;
struct SmPSIn {float4 tex[5];};
struct SmPSOut {float4 oc[1];};
struct SmPSAux {float lodbias[4];};
static void sm_body(thread SmPSIn &si, thread SmPSOut &so, thread SmPSAux &aux, texture2d<float> tex3, sampler smp3, constant float4 *c) {
    so.oc[0]=tex3.sample(smp3, (si.tex[1]).xy / (si.tex[1]).w, bias(aux.lodbias[3]));
}
struct SmPSStageIn {
    float4 pos [[position]];
    float4 texcoord3 [[user(texcoord3)]];
    float4 texcoord4 [[user(texcoord4)]];
};
struct SmPSStageOut {float4 c0 [[color(0)]];};
fragment SmPSStageOut sm_main(SmPSStageIn pi [[stage_in]], constant float4 *c [[buffer(0)]], texture2d<float> tex3 [[texture(3)]], sampler smp3 [[sampler(3)]]) {
    SmPSIn si={};si.tex[1]=float4(.5,.5,0,1);SmPSAux aux={};
    si.tex[4] = pi.texcoord4;
    SmPSOut so;
    sm_body(si,so,aux,tex3,smp3,c);SmPSStageOut r;r.c0=so.oc[0];
    return r;
}
'''


def run(args, expected=0):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True)
    if p.returncode == 3:
        print(p.stdout.strip())
        raise SystemExit(3)
    assert p.returncode == expected, f'{args[0]}: {p.returncode}\n{p.stdout[-2000:]}\n{p.stderr[-5000:]}'
    return p.stdout.strip()


def main():
    materials = (RT / 'materials.m').read_text()
    helpers = materials.split('static const char ripple_ps_helpers[] =', 1)[1].split('// Edits *s', 1)[0]
    prefix = '\nstatic int ripple_provider = 1;\n' + '\n'.join('#define PORTS_RIPPLE_' + key + ' X360S_RIPPLE_' + key for key in ('GLINT_T', 'GLINT', 'REFL_T', 'REFL', 'SCALE2', 'TILE')) + '\n'
    fixture = prefix + '''#define X360S_RIPPLE_GLINT_T "0.25"
#define X360S_RIPPLE_GLINT "0.25"
#define X360S_RIPPLE_REFL_T "0.5"
#define X360S_RIPPLE_REFL "0.5"
#define X360S_RIPPLE_SCALE2 "1.5"
#define X360S_RIPPLE_TILE "0.1"
static const char ripple_ps_helpers[] =''' + helpers
    fixture += '\n'.join(function(materials, n) for n in ('edit', 'count', 'ripple_ps_patch', 'ripple_vs_patch'))
    fixture += '\n#include "roadfilm.m"\nint postfx_look_variant(const char *v){(void)v;return 0;}\n'
    with tempfile.TemporaryDirectory(prefix='rain-cover-gpu-') as directory:
        tmp = Path(directory)
        header = tmp / 'materials.inc'
        header.write_text(fixture)
        binary = tmp / 'fixture'
        run(['clang', '-O1', '-g', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-I', RT, '-I', ROOT / 'recomp',
             '-DRAIN_COVER_MATERIAL_FIXTURE="' + str(header) + '"', RT / 'rain_cover_gpu_test.m',
             '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', binary])
        vs, ps = tmp / 'base.vs', tmp / 'base.ps'
        vs.write_text(VERTEX)
        ps.write_text(PIXEL)
        outvs, outps = tmp / 'cover.vs', tmp / 'cover.ps'
        run([binary, '--emit', vs, outvs, 0])
        run([binary, '--emit', ps, outps, 1])
        print('fixed: ' + run([binary, '--gpu', outvs, outps]))
        broken = outps.read_text().replace(' > pi.rain_world.z+0.35f', ' < pi.rain_world.z-0.35f')
        assert broken != outps.read_text()
        wrong = tmp / 'inverted.ps'
        wrong.write_text(broken)
        print('inverted: ' + run([binary, '--gpu', outvs, wrong], expected=1))
        filmed = tmp / 'cover-film.ps'
        run([binary, '--emit-film', ps, filmed, 1])
        film_result = run([binary, '--gpu-film', outvs, filmed])
        assert '0 failures / 48 channels' in film_result
        print('water film dry/wet composition: ' + film_result)
        # The ordinary shelter program is exactly the film-omitted control.
        omitted_result = run([binary, '--gpu-film', outvs, outps], expected=1)
        assert '18 failures / 48 channels' in omitted_result
        print('film patch omitted: ' + omitted_result)
        # The installed corpus is optional; the numeric fixture above is bare.
        hashes = {int(h, 16) for h in re.findall(r'RIPPLE_(?:VS|PS)\("[^"]+", 0x([0-9A-F]+)ULL', materials)}
        def eligible(path):
            value = 1469598103934665603
            for byte in path.read_bytes():
                value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
            return value in hashes
        shaders = [s for s in sorted((ROOT / 'host/shaders/testdata/worldreflect').glob('*.bin'))
                   if re.search(r'_ps_2_(?:0|x)\.bin$', s.name) or re.search(r'^t[01]_.*_vs_1_1\.bin$', s.name)]
        if shaders:
            assert len(shaders) == 8 and all(eligible(s) for s in shaders), 'expected six pixel and two vertex ripple programs'
            translator = tmp / 'translate.cpp'
            translator.write_text('''#include "sm_translate.h"
#include <fstream>
#include <vector>
int main(int argc,char**argv){if(argc!=3)return 2;std::ifstream f(argv[1],std::ios::binary|std::ios::ate);
if(!f)return 2;size_t n=f.tellg();f.seekg(0);std::vector<uint32_t>w(n/4);f.read((char*)w.data(),n);
SmOptions o;sm_default_options(&o);SmResult r={};if(sm_translate(w.data(),w.size(),&o,&r))return 2;
std::ofstream(argv[2])<<r.msl;sm_result_free(&r);return 0;}
''')
            trans = tmp / 'translate'
            run(['clang++', '-std=c++17', '-O1', '-I', ROOT / 'host/shaders', translator,
                 ROOT / 'host/shaders/sm_parse.cpp', ROOT / 'host/shaders/sm_msl.cpp', '-o', trans])
            count = 0
            for shader in shaders:
                if not re.search(r'_(vs|ps)_', shader.name):
                    continue
                raw, patched = tmp / 'translated.metal', tmp / 'covered.metal'
                run([trans, shader, raw])
                run([binary, '--emit', raw, patched, 0 if '_vs_' in shader.name else 1])
                run([binary, '--compile', patched])
                count += 1
                if '_ps_' in shader.name:
                    run([binary, '--emit-film', raw, patched, 1])
                    run([binary, '--compile', patched])
                    count += 1
            print(f'Installed production worldreflect sources compiled: {count}')
    print('rain_cover_gpu_test PASS')


if __name__ == '__main__':
    main()
