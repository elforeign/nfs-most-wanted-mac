"""Production lamp/receiver Metal proof; no game launch.

Runs headlights.m's actual main-view preparation, current-frame draw admission,
shader construction and resource binding against the translated world/car
programs. Mock guest calls provide only the bounded scene graph/queue; all
rasterization, depth maps, texture fetches and lighting execute on Metal.
The fixture includes world, road, building and car shader families. Missing
private shader bytecode or a Metal device exits 3. The suite autodiscovers this
Python driver; its paired Objective-C harness is headlights_test.m.
Hour gate: level .5 packs lamp w .5, level 0 prepares nothing (377 checks / 0
failures); gate and w scale removed: 3 failures.
Metal proof: production 298 checks / 0 failures; projection removed 12/244
failures, shadows removed 4/244, inverse-square removed 6/244 (ratio 2 vs 4).
25/50 m RGB .0100903809/.00252259523; unchanged alpha .222; three blockers 0.
Off/main: guest calls 0/0, allocations 0/0, textures 0/0, draws 1/1.
107 production/composed shader libraries compile, including 18 rain-cover/film
and 8 clearcoat compositions (431 checks / 0 failures). The original fragment
slots fail Metal compilation with two resource-reservation errors on the first
sheltered receiver; the fixed slots compile every composition.
Cache warming: valid / HL_RECEIVER|CLEARCOAT / valid records warm 2 shaders;
deliberately restoring the invalid-variant break warms 1 (On and Off alike).
Absolute-coordinate control:
1/248 failures, grazing-road visibility 0 versus 1; resident BVH selections 5/5.
The translated visibility and TreeCull functions run, including a blocker outside
main-camera clipping. Runtime integration is checked independently
of the optical helper, including the pre-opaque hook, composite variant keys,
main-view admission, fresh state and the native Off allocation/draw baseline.
Fix pass checks (new fixture vs the previous headlights.m): 337 checks / 0 failures
now; 19 failures on the old code: receivers admitted only for the render-target
entry the prepared eView names (old code compared the entry with the eView, so no
receiver drew: receiver_draws 0 vs 22), snapshot bindings restored with exact
reference counts (old: bound buffer 0 refs and freed while bound, cast buffer 2;
now 1/1/0), and a diagnostic list naming headlighton enabling the feature without
the switch (old: disabled).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'


def run(args, expected=0, env=None):
    p = subprocess.run(list(map(str, args)), cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, env=env)
    if p.returncode != expected:
        raise AssertionError(f'{Path(str(args[0])).name}: exit {p.returncode}, expected {expected}\n{p.stdout[-6500:]}')
    return p.stdout



def function(source, name):
    match = re.search(r'^(?:static )?[^\n]*\b' + re.escape(name) + r'\([^{};]*?\)\s*\{', source, re.M)
    assert match, name
    start = source.index('{', match.start()); depth = 0
    for token in re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|[{}]', source[start:], re.S):
        if token[0] == '{': depth += 1
        elif token[0] == '}':
            depth -= 1
            if not depth: return source[match.start():start + token.end()] + '\n'
    raise AssertionError(name)


def initializer(source, name):
    match = re.search(r'^static const char ' + name + r'\[\]\s*=\s*(.*?);\s*\n', source, re.M | re.S)
    assert match, name
    return match[0]


def compatibility_header(tmp):
    # Copy the production generators verbatim, including generated game literals.
    # Only unrelated runtime option callbacks are inert fixture dependencies.
    material = (RT / 'materials.m').read_text()
    real = (RT / 'realworld_materials.c').read_text()
    text = '#include "../host/shaders/sm_coverage.h"\n'
    text += '#include "gen/pc_shader_lits.inc"\n#include "gen/car_shadow_off.inc"\n#include "gen/x360_shader_lits.inc"\n'
    text += re.search(r'^#define CAR_PAINT_POW_MSL .*?(?=\n\n)', material, re.M | re.S)[0] + '\n'
    text += initializer(material, 'car_paint_msl')
    text += '#include \"gen/port_rain_ripples.inc\"\nstatic int ripple_provider = 1;\n'
    for name in ('ripple_ps_helpers', 'port_ripple_ps_helpers', 'parallax_ps_helpers'):
        text += initializer(material, name)
    text += 'static int car_light_hdr(void){return 1;}\n'
    for name in ('edit', 'count', 'dup_replace', 'car_vs_patch', 'car_vs_nm_patch', 'car_t0_ps_patch',
                 'ripple_vs_patch', 'ripple_ps_patch', 'parallax_ps_patch'):
        text += function(material, name)
    text += initializer(real, 'optics')
    for name in ('replace', 'realworld_materials_vertex', 'realworld_materials_pixel'):
        text += function(real, name)
    coat = (RT / 'clearcoat.c').read_text()
    text += initializer(coat, 'coat_math')
    for name in ('replace_once', 'clearcoat_patch', 'clearcoat_vertex', 'clearcoat_t0_patch'):
        text += function(coat, name)
    # The fixture supplies the already generated production ripple source to
    # the shelter wrapper, including its optional production film patch.
    text += '''
static const char *composition_base;
char *roadfilm_patch(const char *);
char *mat_replace(const uint32_t *t,uint32_t n,const SmResult *r,int *id) {
    return strdup(composition_base);
}
char *mat_roadfilm_shader(const uint32_t *t,uint32_t n,const SmResult *r,int *id) {
    return roadfilm_patch(composition_base);
}
'''
    rain = (RT / 'rain_cover.m').read_text()
    for name in ('cover_edit', 'rain_cover_shader'):
        text += function(rain, name)
    (tmp / 'headlights_compat.inc').write_text(text)
    translated = '#include "runtime.h"\nvoid f_006BE800(Cpu *);\nvoid f_006CF2B0(Cpu *);\nvoid f_004FCA70(Cpu *);\nvoid f_00729120(Cpu *);\n'
    for file, name in (('speed_40.c', 'f_006CF2B0'), ('speed_45.c', 'f_00729640')):
        translated += function((ROOT / 'recomp/out' / file).read_text(), name)
    (tmp / 'headlights_culling.inc').write_text(translated)

def cache_warm_fixture():
    # Execute the full production reader; only compilation/dispatch dependencies
    # are inert. Distinct token payloads keep the two valid records independent.
    render = (RT / 'render.m').read_text()
    warmer = function(render, 'shader_cache_warm')
    source = r'''
#include "game_select.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define HEADLIGHTS_CORE_ONLY
#include "headlights.h"
#include "materials.h"
#include "roadfilm.h"
#include "rain_cover.h"
#include "realworld_materials.h"
#include "clearcoat.h"
typedef struct { uint64_t key; } ShaderEntry;
static ShaderEntry entries[3], *shaders[3];
static int nshaders, submitted, enabled;
static const char *fixture_path;
static int shader_q, shader_workers[4];
#define DISPATCH_QUEUE_SERIAL 0
static int dispatch_queue_create(const char *name, int flags) { return 0; }
static void cache_path(char *out, size_t size) { snprintf(out, size, "%s", fixture_path); }
int headlights_enabled(void) { return enabled; }
int rain_cover_on(void) { return 1; }
static void shader_catalogue_warm(void) {}
static int shader_shape(const uint32_t *t, uint32_t n, uint64_t hash) { return 0; }
static SmOptions shader_options(int shape, const SmOptions *opt) { return *opt; }
static ShaderEntry *new_entry(uint64_t key, const uint32_t *t, uint32_t n,
                              const SmOptions *opt, uint32_t variant) {
    assert(nshaders < 3);
    ShaderEntry *e = &entries[nshaders];
    e->key = key; shaders[nshaders++] = e; return e;
}
static void shader_submit(ShaderEntry *e) { ++submitted; }
'''
    source += function(render, 'fnv')
    source += function(render, 'shader_cache_variant_valid')
    main = r'''
int main(int argc, char **argv) {
    assert(argc == 2); fixture_path = argv[1];
    FILE *f = fopen(fixture_path, "wb"); assert(f);
    uint32_t hdr[] = {0x4353464Eu, sizeof(SmOptions) + 4};
    assert(fwrite(hdr, sizeof hdr, 1, f) == 1);
    uint32_t variants[] = {0, HL_RECEIVER | MAT_VARIANT_CLEARCOAT, 0};
    for (unsigned i = 0; i < 3; ++i) {
        SmOptions opt = {0}; uint32_t n = 2, tokens[] = {i + 1, 0xffff};
        assert(fwrite(&opt, sizeof opt, 1, f) == 1);
        assert(fwrite(&variants[i], 4, 1, f) == 1);
        assert(fwrite(&n, 4, 1, f) == 1);
        assert(fwrite(tokens, sizeof tokens, 1, f) == 1);
    }
    assert(fclose(f) == 0);
    int failed = 0;
    for (enabled = 0; enabled <= 1; ++enabled) {
        nshaders = submitted = 0;
        shader_cache_warm();
        printf("cache warm %s: %d\n", enabled ? "On" : "Off", submitted);
        if (submitted != 2) failed = 1;
    }
    return failed;
}
'''
    skip = re.search(r'if \(!shader_cache_variant_valid\(variant\)\) \{.*?\n            \}', warmer, re.S)
    assert skip, 'missing invalid-variant skip'
    broken = warmer[:skip.start()] + 'if (!shader_cache_variant_valid(variant)) break;' + warmer[skip.end():]
    with tempfile.TemporaryDirectory(prefix='headlights-cache-') as folder:
        tmp = Path(folder)
        for name, reader, expected in (('fixed', warmer, 0), ('broken', broken, 1)):
            code = tmp / f'{name}.c'
            code.write_text(source + reader + main)
            exe = tmp / name
            run(['clang', '-std=c11', '-I', RT, '-I', ROOT / 'recomp', code, '-o', exe])
            output = run([exe, tmp / 'cache.bin'], expected=expected)
            count = 2 if name == 'fixed' else 1
            assert f'cache warm Off: {count}' in output
            assert f'cache warm On: {count}' in output
            print(f'{name}: {output.strip()}', flush=True)


def integration():
    mirror = (RT / 'mirror.c').read_text()
    hook = mirror.split('void f_00723FA0(Cpu *c)', 1)[1].split('\n}', 1)[0]
    assert hook.index('headlights_prepare(') < hook.index('f_00723FA0_orig(')
    render = (RT / 'render.m').read_text()
    assert '#include "headlights.h"' in render
    assert 'vse->variant|headlight' in render and 'pse->variant|headlight' in render
    assert 'headlights_bind(enc,&headlight_constants,headlight==HL_RECEIVER)' in render
    assert 'if(headlights_casting())rt0=headlights_colour()' in render
    assert 'if(headlights_casting())dst=headlights_depth()' in render
    assert 'if(headlights_casting()){geometry();headlights_drawn(0);return;}' in render
    assert 'headlights.m' in (RT / 'build.sh').read_text()
    assert 'headlight_profile.c' in (RT / 'build.sh').read_text()
    assert 'headlights_test.py / headlights_test.m' in (RT / 'offline_tests.sh').read_text()
    assert 'NFS_HEADLIGHT_PROJECTION\topt\toff\t' in (RT / 'switches.tsv').read_text()
    print('pre-opaque hook / composite shaders / target-depth binding / Off default PASS', flush=True)


def main():
    integration()
    cache_warm_fixture()
    data = ROOT / 'host/shaders/testdata'
    required = [data / 'world/t0_world_p0_vs_1_1.bin',
                ROOT / 'recomp/out/speed_40.c', ROOT / 'recomp/out/speed_45.c',
                *(RT / 'gen' / f for f in ('x360_shader_lits.inc', 'pc_shader_lits.inc', 'car_shadow_off.inc'))]
    if not all(p.exists() for p in required) or any(
            len(list((data / group).glob('t[0-5]_*_ps_*.bin'))) != 6
            for group in ('world', 'worldnormalmap', 'worldreflect', 'car')):
        print('SKIP: needs private shader fixtures, generated literals and translated culling'); return 3
    with (RT / 'gen/x360_shader_lits.inc').open() as generated:
        if '#define X360_SHADER_LITS 1' not in generated.read(4096):
            print('SKIP: needs generated X360 shader literals'); return 3
    with tempfile.TemporaryDirectory(prefix='headlights-') as folder:
        tmp = Path(folder)
        compatibility_header(tmp)
        objects = []
        for file in ('sm_parse.cpp', 'sm_msl.cpp'):
            obj = tmp / (Path(file).stem + '.o')
            run(['clang++', '-std=c++17', '-O1', '-c', ROOT / 'host/shaders' / file, '-o', obj])
            objects.append(obj)
        executable = tmp / 'headlights'
        run(['clang', '-O1', '-g', '-fobjc-arc', '-I', ROOT / 'recomp', '-I', ROOT / 'recomp/out', '-I', RT, '-I', tmp,
             RT / 'headlights_test.m', RT / 'headlight_profile.c', *objects,
             '-lc++', '-framework', 'Foundation', '-framework', 'Metal', '-o', executable])
        env = dict(os.environ, NFS_HEADLIGHT_PROJECTION='off', MTL_DEBUG_LAYER='1')
        normal = subprocess.run([executable, data], cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env)
        if normal.returncode == 3:
            print(normal.stdout[-1000:]); return 3
        if normal.returncode:
            raise AssertionError(f'production exit {normal.returncode}\n{normal.stdout[-6500:]}')
        evidence = [normal.stdout]
        print(normal.stdout.strip(), flush=True)
        for mutation in ('projection', 'shadows', 'falloff', 'absolute'):
            result = run([executable, data, mutation], expected=1, env=env)
            evidence.append(result)
            print(result[result.rfind('headlights '):].strip(), flush=True)
        cache = Path(os.environ.get('NFS_METAL_CACHE', ROOT / '.metal-cache'))
        cache.mkdir(parents=True, exist_ok=True)
        (cache / 'headlights-proof.log').write_text('\n'.join(evidence))
        print('headlights_test PASS: real shaders, admission, depth, alpha and four failing controls')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
