"""Production lamp/receiver Metal proof; no game launch.

Runs headlights.m's actual main-view preparation, current-frame draw admission,
shader construction and resource binding against the translated world/car
programs. Mock guest calls provide only the bounded scene graph/queue; all
rasterization, depth maps, texture fetches and lighting execute on Metal.
The fixture includes world, road, building and car shader families. Missing
private shader bytecode or a Metal device exits 3. The suite autodiscovers this
Python driver; its paired Objective-C harness is headlights_test.m.
Wall edge regression: angular grayscale step 3.429225/255 (maximum 10),
original unfeathered cut-off 149.631958/255 fails. Perspective wall visibility
standard deviation 0/255 (maximum 1); original absolute packing 68.590138/255
with mean visibility .763532 fails. The pinned original fragments below restore
both defects in one independently compiled broken control.
Hour gate: level .5 packs lamp w .5, level 0 prepares nothing (377 checks / 0
failures); gate and w scale removed: 3 failures.
Dark asphalt (1% linear reflectance) at 50 m: .01655 radiance (>= .01);
old conversion .00000202 fails. Removing the clock's night conversion rejects the control.
Live M3 depth proof: 40–60 m road 36.246419/255, no beam 8.878215 (limit 30).
Car/world luma correction .301740; restoring the fixed rig gives 1 (limit .5).
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
import hashlib
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
ORIGINAL_LAMP_PACK = '    for(int i=0;i<2;++i){headlights_pack_lamp(&lamps[i],&prepared.lamps[i]);prepared.lamps[i].position[3]*=level;} /* w: lamp strength */'
ORIGINAL_LAMP_PACK_SHA256 = '55bf0a8b99b83913dbabf94bb023c40e03e34e3fcc1c253ae592bfd0549fca38'
ORIGINAL_COOKIE_RETURN = '" return mix(mix(hl_cd[y*HL_NX+x],hl_cd[y*HL_NX+x+1],u),mix(hl_cd[(y+1)*HL_NX+x],hl_cd[(y+1)*HL_NX+x+1],u),t);\\n"'
ORIGINAL_COOKIE_SHA256 = '36733f1eba9c24704e6290693a1be455e597b7f8e14b14d1892c39392c9628e3'
ORIGINAL_WORLD_PACK = '    for(int i=0;i<4;++i)for(int j=0;j<4;++j) {\n        world[i][j]=0;for(int k=0;k<4;++k)world[i][j]+=dev.vsc[wvp+k][i]*inverse_vp[k][j];\n        out->world[j][i]=world[i][j];\n    }\n    if(!hl_inverse(world,inverse)){headlights_missed();return 0;}\n'
ORIGINAL_WORLD_SHA256 = '049ff58b853b291c6cada25b1e72e57ce61cbe21bea62332ce4dcae0a7f0e260'
def switch_registry(root):
    """runtime/switches.tsv, or None in a public copy. The registry is dev-only (tools/publish/denylist.txt keeps it out
    of the public repos), so only a tree without tools/publish may lack it; in the dev repo a missing file still fails."""
    path = root / 'runtime/switches.tsv'
    if path.is_file() or (root / 'tools/publish').is_dir():
        return path.read_text()
    print('skip: switch registry checks (runtime/switches.tsv is not exported to the public repo)', flush=True)
    return None



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
    registry = switch_registry(ROOT)
    assert registry is None or 'NFS_HEADLIGHT_PROJECTION\topt\ton\t' in registry
    print('pre-opaque hook / composite shaders / target-depth binding / On default PASS', flush=True)


def main():
    assert hashlib.sha256(ORIGINAL_LAMP_PACK.encode()).hexdigest() == ORIGINAL_LAMP_PACK_SHA256
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
        # Recompile the pinned original packing fragment, with every other
        # production dependency unchanged. The clock still requests night
        # units; this old path drops them, reproducing the original weak beam.
        source = (RT / 'headlights.m').read_text()
        current = re.search(r'^    for\(int i=0;i<2;\+\+i\)\{headlights_pack_lamp\([^\n]+', source, re.M)[0]
        old = tmp / 'old-pack.m'
        old.write_text(source.replace(current, ORIGINAL_LAMP_PACK, 1))
        binary = tmp / 'old-pack'
        run(['clang', '-O1', '-g', '-fobjc-arc', '-I', ROOT / 'recomp', '-I', ROOT / 'recomp/out', '-I', RT, '-I', tmp,
             f'-DHEADLIGHTS_SOURCE="{old}"', RT / 'headlights_test.m', RT / 'headlight_profile.c', *objects,
             '-lc++', '-framework', 'Foundation', '-framework', 'Metal', '-o', binary])
        result = run([binary, data, 'old-pack'], expected=1, env=env)
        assert 'night road at 50m radiance 2.0199' in result, result
        evidence.append(result)
        print(result[result.rfind('night road at 50m'):].strip(), flush=True)
        # Restore only the two pinned original optical/coordinate fragments.
        assert hashlib.sha256(ORIGINAL_COOKIE_RETURN.encode()).hexdigest() == ORIGINAL_COOKIE_SHA256
        assert hashlib.sha256(ORIGINAL_WORLD_PACK.encode()).hexdigest() == ORIGINAL_WORLD_SHA256
        broken = source.replace('u),t)*edge;', 'u),t);', 1)
        assert ORIGINAL_COOKIE_RETURN in broken
        start = broken.index('    for(int i=0;i<4;++i)for(int j=0;j<4;++j) {', broken.index('int headlights_draw('))
        end = broken.index('    for(int i=0;i<3;++i)for(int j=0;j<3;++j)out->normal', start)
        broken = broken[:start] + ORIGINAL_WORLD_PACK + broken[end:]
        original = tmp / 'original-wall.m'
        original.write_text(broken)
        binary = tmp / 'original-wall'
        run(['clang', '-O1', '-g', '-fobjc-arc', '-I', ROOT / 'recomp', '-I', ROOT / 'recomp/out', '-I', RT, '-I', tmp,
             f'-DHEADLIGHTS_SOURCE="{original}"', RT / 'headlights_test.m', RT / 'headlight_profile.c', *objects,
             '-lc++', '-framework', 'Foundation', '-framework', 'Metal', '-o', binary])
        result = run([binary, data, 'original-wall'], expected=1, env=env)
        assert float(re.search(r'beam angular edge step ([0-9.]+)', result)[1]) > 10, result
        assert float(re.search(r'wall visibility grain ([0-9.]+)', result)[1]) > 1, result
        evidence.append(result)
        print('\n'.join(line for line in result.splitlines() if 'edge step' in line or 'visibility grain' in line), flush=True)
        cache = Path(os.environ.get('NFS_METAL_CACHE', ROOT / '.metal-cache'))
        cache.mkdir(parents=True, exist_ok=True)
        (cache / 'headlights-proof.log').write_text('\n'.join(evidence))
        print('headlights_test PASS: real shaders, admission, depth, alpha and six failing controls')
    return 0


def capture_proof(folder, stem):
    """Held midnight proof: 40–60 m road >= 30/255; optional old-rig comparison.

    The missing-beam pass must fail the reach limit. When supplied, the old-rig
    pass must also fail its car/world correction limit.
    A raw car/road ratio limit of 1.5 rejected a scaled red car at 1.631915:
    unlike the rig attenuation, paint and road reflectance are not identical.
    The held ratio of those ratios cancels that material difference; restoring
    the fixed rig gives 1 and fails .5. Road positions use captured scene depth,
    avoiding the .65 m reference plane's mismatch on sloping actual scenery.
    """
    import numpy as np
    import struct
    from PIL import Image
    frames = {}
    variants = ['', '_headlightoff']
    if (folder / (stem + '_nightold.png')).exists():
        variants.append('_nightold')
    for variant in variants:
        pixels = np.asarray(Image.open(folder / (stem + variant + '.png')).convert('RGB').resize((960, 602)), dtype=float)
        frames[variant] = np.sum(pixels * [.2126, .7152, .0722], axis=2)
    def mean(pixels, box):
        x0, y0, x1, y1 = box
        return float(pixels[y0:y1, x0:x1].mean())
    body, road = (415, 415, 540, 485), (395, 312, 550, 335)
    ratios = [mean(frames[v], body) / mean(frames[v], road) for v in ('_headlightoff', '_nightold')] if '_nightold' in frames else None
    frame = int(stem.removeprefix('frame_'))
    match = re.search(rf'headlights_view: frame={frame} geometry=([^\n]+)', (folder / 'events.log').read_text())
    assert match, 'missing lamp/view diagnostic for this requested shot'
    geometry = np.fromstring(match[1], sep=',')
    assert len(geometry) == 28
    lamp, right, up, forward = geometry[:12].reshape(4, 3)
    vp = geometry[12:].reshape(4, 4)
    candidates = []
    for path in folder.glob('ssao_depth_*.aodz'):
        depth_frame = int(path.name.split('_')[2])
        if not frame <= depth_frame <= frame + 8:
            continue
        with path.open('rb') as f:
            magic, w, h, *_ = struct.unpack('<4sIIffff', f.read(28))
        if magic == b'AOZ2' and abs(w/h - 960/602) < .02:
            candidates.append((depth_frame - frame, path, w, h))
    assert candidates, 'missing held scene depth; include ssaon in BOOT_DIAG_LOOK_AB'
    _, path, w, h = min(candidates)
    depth = np.fromfile(path, dtype='<f4', offset=28).reshape(h, w)
    xs = np.minimum(w-1, ((np.arange(960)+.5)*w/960).astype(int))
    ys = np.minimum(h-1, ((np.arange(602)+.5)*h/602).astype(int))
    z = depth[np.ix_(ys, xs)]
    x, y = np.meshgrid(2*(xs+.5)/w-1, 1-2*(ys+.5)/h)
    clip = np.stack((x, y, z, np.ones_like(z)), axis=2)
    world = np.einsum('...i,ij->...j', clip, np.linalg.inv(vp))
    world = world[:, :, :3] / world[:, :, 3:4]
    relative = world - lamp
    along = np.sum(relative * forward, axis=2)
    lateral = np.sum(relative * right, axis=2)
    height = np.sum(relative * up, axis=2)
    normal = np.cross(np.gradient(world, axis=1), np.gradient(world, axis=0))
    normal_length = np.linalg.norm(normal, axis=2)
    flat = (normal_length > 1e-8) & (abs(np.sum(normal * up, axis=2)) >= .8 * normal_length)
    road_surface = (abs(lateral) <= 3) & (height > -5) & (height < 2) & flat & (z < 1)
    mask = (along >= 40) & (along <= 60) & road_surface
    assert mask.sum() >= 25, f'insufficient road pixels: {mask.sum()}'
    beam = [float(frames[v][mask].mean()) for v in ('', '_headlightoff')]
    if ratios:
        correction = ratios[0] / ratios[1]
        print(f'car/world night luma correction {correction:.6f}; broken fixed rig 1 (limit .5); raw ratios {ratios[0]:.6f}/{ratios[1]:.6f}')
        assert correction <= .5, 'car/world visible mismatch'
    print(f'40-60m road luma {beam[0]:.6f}; broken no beam {beam[1]:.6f} (minimum 30/255; {mask.sum()} pixels)')
    for lo, hi in ((5, 10), (10, 20), (20, 30)):
        near = (along >= lo) & (along < hi) & road_surface
        values = [float(frames[v][near].mean()) if near.any() else float('nan') for v in ('', '_headlightoff')]
        print(f'{lo}-{hi}m road luma On/Off {values[0]:.6f}/{values[1]:.6f}; pixels={near.sum()}')
    for variant in variants:
        pixels = frames[variant]
        print(f'{variant or "On"} body/road/wall luma {mean(pixels, body):.6f}/{mean(pixels, road):.6f}/{mean(pixels, (680, 265, 735, 320)):.6f}')
    assert beam[0] >= 30 > beam[1], 'road remains unreadable or ineffective broken control'
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--capture-proof':
        raise SystemExit(capture_proof(Path(sys.argv[2]), sys.argv[3]))
    raise SystemExit(main())
