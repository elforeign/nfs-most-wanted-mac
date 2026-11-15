"""Objects-only motion after scene MSAA resolve; camera motion inside the consumer.
Production: 65266 checks / 0 failures (64226 without timing checks).
Controls (checks / failures): wrong sign 64226 / 32480;
missing object 64144 / 28565; stale VP in shared function 64226 / 3920;
object override omitted 64226 / 28562; MSAA target 62310 / 3;
mismatched scene samples 64226 / 1; destructible left static 64217 / 5040;
stationary replay 64226 / 22; unresolved depth in function 1534 / 560;
unthrottled diagnostics 64226 / 2. All controls exit 1 numerically.
Independent SHA-256-pinned prior object/camera shader fragments produce the
stored combined reference. The production object atlas plus the shared MSL
function, included in a mock consumer, matches all 24640 reference components
exactly. Closed-form pan, body translation/rotation, tilted wheels, destructibles,
occlusion, object override and covered zero motion also pass at pixel centres.
Coverage is explicit R8 alongside 1x RG16F; an empty next frame clears old coverage; uncovered RG16F is undefined.
Minimum-resolved-depth proof retains a 4x scene with distinct depth samples.
Precision tolerance: 1.1e-3 relative / 2e-7 absolute for the analytic oracle.
108 retail translations and 91 custom shader cases compile. The exact request
guard enters zero temporal functions in 1000 disabled calls; scene/view scoping
runs. The actual production command buffer encodes one objects render pass,
with no camera shader entry point or reprojection encoder in production.
BOOT_DIAG counts instanced draws and reports isolated stage-counter GPU time;
a deterministic clock proves one-second throttling and empty-frame counts.
Off allocates no counters and attaches no diagnostic completion handlers.
3840x2160 fixture: scene MSAA 4x, six warmups, median N=31 objects-only timings.
Alternating paired control clears RG16F too: 0.239875 vs 0.180000 ms realistic,
0.946750 vs 0.901750 ms worst. Shipping clears only coverage.
Thirty vehicle proxies each have one body and four articulated wheels. Realistic:
9 bodies, 36 wheels, 105 parked draws skipped, 19.3646% coverage. Worst:
30 bodies, 120 wheels, no skips, 64.5486% coverage. Depth is initialized from
the same geometry. Proxies establish counts/coverage, not real mesh/game cost.
API validation enabled; execution conditions uncontrolled, not solo game timings.
The mock consumer and pinned combined pass are excluded from objects timings.
Proof: .metal-cache/temporal-motion-proof.txt. No game or emulation.
Autodiscovered by offline_tests.sh; exit 3 without Metal/retail shader fixtures.
"""
from pathlib import Path
import os
import hashlib
import re
import runpy
import shutil
import subprocess
import tempfile

import sys as _source_sys
_source_sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_file_fixture import mw_source

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'


def function(source, name):
    hit = re.search(r'^[^\n]*\b' + name + r'\([^;]*?\)\s*\{', source, re.M)
    assert hit, name
    depth = 1
    for token in re.finditer(r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/|[{}]', source[hit.end():], re.S):
        if token[0] == '{':
            depth += 1
        elif token[0] == '}':
            depth -= 1
        if not depth:
            return source[hit.start():hit.end() + token.end()]
    raise AssertionError(name)


def run(command, *, env=None):
    p = subprocess.run(list(map(str, command)), cwd=ROOT, capture_output=True, text=True, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-5000:]
    return p.stdout


def main():
    required = [RT / 'gen' / name for name in ('x360_shader_lits.inc', 'pc_shader_lits.inc',
                'car_shadow_off.inc', 'x360_shader_body.inc')]
    required += [ROOT / 'host/shaders/testdata' / name for name in (
        'world/t0_world_p0_vs_1_1.bin', 'world/t0_world_p0_ps_1_1.bin', 'car/t0_car_p0_vs_1_1.bin')]
    if any(not path.is_file() for path in required) or not re.search(
            r'#define X360_SHADER_BODIES\s+1', required[3].read_text()):
        print('SKIP: production shader fixtures/generated bodies absent')
        return 3
    if not shutil.which('xcrun'):
        print('SKIP: Metal compiler absent')
        return 3
    motion = (RT / 'temporal_render.m').read_text()
    assert 'reproject_encoder' not in motion and 'temporal_render_reproject' not in motion
    assert 'motion_reproject' not in motion and 'fragment half2 reproject' not in motion
    camera = function(motion, 'temporal_render_camera_msl')
    assert 'inline half2 temporal_camera_motion' in camera
    assert 'fragment ' not in camera and 'kernel ' not in camera
    render = mw_source(RT / 'render.m')
    target = (RT / 'render_target.m').read_text()
    state = (RT / 'temporal_state.c').read_text()
    internal = (RT / 'render_internal.h').read_text()
    material = (RT / 'materials.m').read_text()
    assert 'temporal_render_resolve(mtl,cmd,temporal_draw_target,depth,view)' in target
    assert 'if (motion) temporal_pose_commit(view,state->current.serial)' in target
    assert 'render_temporal_motion(' in internal
    assert 'temporal_view_accept_target' in state
    assert 'int rigid=pose!=NULL && !pse->refl.writes_depth;' in render
    assert 'motion_tee=temporal_render_record' in render
    assert 'if(temporal_state_requested && temporal_draw_target' in render
    assert 'if(rigid && temporal_render_moved(' in render
    assert 'temporal_render_attach' not in render
    assert 'temporal_render.m' in (RT / 'build.sh').read_text()
    guard = re.search(r'    if \(temporal_state_requested\) temporal_render_scene\([^;]+;', render)[0]
    ff = render[render.index('enum { RT_TEMP ='):render.index('static FFShader ff_cache')]
    ff = ff.replace('Obj vs, ps;', 'struct { uint32_t *tokens, ntokens; } vs, ps;')
    ff += function(render, 'ff_build')
    paint = material[material.index('#define CAR_PAINT_POW_MSL'):material.index('\n// BOOT_DIAG_CARPAINT_AB=1')]
    with tempfile.TemporaryDirectory(prefix='temporal-motion-') as temporary:
        td = Path(temporary)
        (td / 'temporal_motion_guard.inc').write_text(guard + '\n')
        (td / 'temporal_destructible_hook.inc').write_text(function(material, 'f_0073B9C0') + '\n')
        (td / 'temporal_motion_classify.inc').write_text(function(material, 'mat_temporal_static') +
            '\nstatic int fog_x360(void){return 1;}\n' + function(render, 'fog_patch') + '\n')
        (td / 'temporal_motion_scene.inc').write_text('static ' + function(
            (RT / 'temporal_render.m').read_text(), 'temporal_render_scene').replace('temporal_render_scene(', 'shipping_scene(', 1) + '\n')
        (td / 'temporal_motion_sources.inc').write_text(
            '#include "../host/shaders/sm_coverage.h"\n'
            '#include "gen/x360_shader_lits.inc"\n#include "gen/pc_shader_lits.inc"\n#include "gen/car_shadow_off.inc"\n' + ff + '\n' + paint + '\n')
        archive = td / 'libsm.a'
        objects = []
        for source in ('sm_parse.cpp', 'sm_msl.cpp'):
            obj = td / (source + '.o')
            run(['clang++', '-std=c++17', '-O2', '-I', ROOT / 'host/shaders', '-c', ROOT / 'host/shaders' / source, '-o', obj])
            objects.append(obj)
        run(['ar', 'rcs', archive, *objects])
        # Compile the adapter against every retail translation and every custom
        # source emitted by the production builders (all input scalar types,
        # techniques, shells and shadow sampler branches).
        adapter = td / 'adapt.cpp'
        adapter.write_text(r'''
#include "sm_translate.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
int main(int argc,char **argv){
    if(argc!=5)return 2;FILE *f=fopen(argv[1],"rb");if(!f)return 2;
    fseek(f,0,SEEK_END);long bytes=ftell(f);rewind(f);std::vector<char> data(bytes+1);fread(data.data(),1,bytes,f);fclose(f);
    int stage=atoi(argv[2]);SmResult r={};const char *source=data.data();
    if(!strcmp(argv[3],"binary")){if(sm_translate((const uint32_t *)data.data(),bytes/4,nullptr,&r))return 2;source=r.msl;stage=r.shader_type;}
    char *motion=sm_temporal_variant(source,stage);if(!motion)return 1;
    f=fopen(argv[4],"wb");if(!f)return 2;fwrite(motion,1,strlen(motion),f);fclose(f);free(motion);sm_result_free(&r);return 0;
}
''')
        adapt = td / 'adapt'
        run(['clang++', '-O2', '-std=c++17', '-I', ROOT / 'host/shaders', adapter, archive, '-o', adapt])
        builder = runpy.run_path(str(RT / 'materials_stub_test.py'))['harness'](material)
        # The no-shadow glass technique uses s1 for colour reflection, never a
        # hardware shadow map. Other glass techniques use s1 for shadow depth.
        call = 'save(name,mat_glass_shader(NULL,0,&pc,stage,si,ui));'
        assert call in builder
        builder = builder.replace(call, 'pc.samplers[0].shadow=shadow && fixture_hash!=0xA8D19FEBB9579A2BULL;\n' + call + '\npc.samplers[0].shadow=shadow;')
        custom_dir = td / 'custom'
        custom_dir.mkdir()
        (custom_dir / 'emit.c').write_text(builder)
        run(['clang', '-std=c11', '-I', RT / 'gen', '-I', RT, custom_dir / 'emit.c', '-o', custom_dir / 'emit'])
        p = subprocess.run([str(custom_dir / 'emit')], cwd=custom_dir, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        custom = {name: (custom_dir / name).read_bytes() if status == 'SOURCE' else None
                  for name, status in (line.split() for line in p.stdout.splitlines())}
        assert {name.split('_')[0] for name, data in custom.items() if data} == {'grass', 'water', 'aux', 'glass', 'trees', 'bone'}
        candidates = [(td / 'custom' / name, int(name.split('_')[1]), 'source') for name, data in custom.items() if data]
        retail = sorted((ROOT / 'host/shaders/testdata').rglob('*.bin'))
        assert retail, 'retail translations missing'
        candidates += [(path, 0, 'binary') for path in retail]
        for index, (path, stage, kind) in enumerate(candidates):
            adapted = td / 'adapted.metal'
            run([adapt, path, stage, kind, adapted])
            run(['xcrun', '-sdk', 'macosx', 'metal', '-fmodules-cache-path=' + str(td / 'modules'), '-c', adapted, '-o', td / 'adapted.air'])
        print(f'motion shader interfaces: retail={len(retail)} custom={len(custom)} compile_failures=0', flush=True)
        # Independent pinned fragments from the prior stored-combined path.
        for name, digest in (
            ('temporal_motion_control.inc', '88761a1eea98a428df220d636012f6324e854c76b5174449376ef6c93e424b57'),
            ('temporal_camera_control.inc', 'ae9fe9a19ee0de315dbcc3bff2b3662ce26fa8370742084618535063849a0763'),
        ):
            assert hashlib.sha256((RT / name).read_bytes()).hexdigest() == digest
        baseline_obj = td / 'baseline.o'
        run(['clang++', '-std=c++17', '-O2', '-I', ROOT / 'host/shaders', '-x', 'c++',
             '-c', RT / 'temporal_motion_control.inc', '-o', baseline_obj])
        # Standalone state implementation is the production lifecycle/pose code.
        state_obj = td / 'state.o'
        run(['clang', '-O2', '-DTEMPORAL_STATE_STANDALONE', '-c', RT / 'temporal_state.c', '-o', state_obj])
        proof = []
        proof_path = Path(os.environ.get('NFS_METAL_CACHE', RT / '.metal-cache')) / 'temporal-motion-proof.txt'
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        for label, mutation in [('production', None), ('wrong-sign', 'TEMPORAL_TEST_WRONG_SIGN'),
                                ('missing-object', 'TEMPORAL_TEST_MISSING_OBJECT'),
                                ('stale-vp', 'TEMPORAL_TEST_STALE_VP'),
                                ('no-object-override', 'TEMPORAL_TEST_NO_OVERRIDE'),
                                ('msaa-motion-target', 'TEMPORAL_TEST_MSAA_TARGET'),
                                ('mismatched-samples', 'TEMPORAL_TEST_MISMATCHED_SAMPLES'),
                                ('destructible-left-static', 'TEMPORAL_TEST_STATIC_DESTRUCTIBLE'),
                                ('stationary-replay', 'TEMPORAL_TEST_REPLAY_STATIONARY'),
                                ('unresolved-depth', 'TEMPORAL_TEST_UNRESOLVED_DEPTH'),
                                ('unthrottled-diag', 'UNTHROTTLED_DIAG')]:
            exe = td / label
            command = ['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-DTEMPORAL_RENDER_TEST',
                       '-I', RT, '-I', td, '-I', ROOT / 'recomp',
                       RT / 'temporal_render_test.m', RT / 'temporal_render.m', RT / 'lampbatch.m', state_obj, archive, baseline_obj,
                       '-lc++', '-framework', 'Metal', '-framework', 'Foundation', '-o', exe]
            if mutation == 'UNTHROTTLED_DIAG':
                broken = td / 'unthrottled.m'
                original = (RT / 'temporal_render.m').read_text()
                old = 'if(!last || now-last>=1000000000ull)'
                assert old in original
                broken.write_text(original.replace(old, 'if((void)last,(void)now,1)'))
                command[command.index(RT / 'temporal_render.m')] = broken
            elif mutation:
                command.append('-D' + mutation)
            run(command)
            p = subprocess.run([str(exe)] + ([] if mutation else ['--cost']), cwd=ROOT, capture_output=True, text=True,
                               env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
            message = label + ': ' + (p.stdout + p.stderr).strip()
            proof.append(message)
            proof_path.write_text('\n'.join(proof) + '\n')
            print(message[-5000:], flush=True)
            if label == 'production' and p.returncode == 3:
                return 3
            assert p.returncode == (1 if mutation else 0), (label, p.returncode)
            match = re.search(r'checks=(\d+) failures=(\d+)', p.stdout)
            assert match and (int(match[2]) > 0 if mutation else int(match[2]) == 0), label
            if mutation not in ('TEMPORAL_TEST_MSAA_TARGET', 'TEMPORAL_TEST_UNRESOLVED_DEPTH'):
                assert 'variants=7/7/8 samples=22/0/0 A2C=1' in p.stdout
    print('temporal_render_test: PASS (shipping shaders, pixel-centre motion, resolved depth, Off guards, stored combined reference, shared consumer and ten broken controls)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
