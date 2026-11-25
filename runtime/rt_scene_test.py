"""Production RT1 capture and real BLAS/TLAS through direct/recorded command paths.

Actual 16x16 GPU grid: both games, direct and recorded, second-frame wrong hit
bits 0/256. Deliberately frozen old instance transform: 16/256 in all four cases.
First frame: 16 hits, 0/256 incorrect bits. One BLAS/two TLAS builds per case.
No game is launched. Off has zero scene allocations/native operations in 1000
calls. Private shader bytes come from the local executable, never the repository.
Autodiscovered by offline_tests.sh; exit 3 without the private fixtures or Metal.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'


class Unavailable(Exception):
    pass


def run(args, *, env=None, expected=0, allow_skip=False):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, env=env)
    if allow_skip and result.returncode == 3:
        raise Unavailable(result.stdout.strip())
    assert result.returncode == expected, (args, result.returncode, result.stdout[-3000:], result.stderr[-3000:])
    return result.stdout


def fnv(data):
    h = 1469598103934665603
    for byte in data:
        h = ((h ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return h


def private_shader():
    image = ROOT / 'recomp/out/carbon/image.json'
    if not image.exists():
        return None
    exe = Path(json.loads(image.read_text())['exe'])
    if not exe.exists():
        return None
    blob = exe.read_bytes()
    adapter = (ROOT / 'recomp/private_runtime/game_rt_scene.h').read_text()
    pinned = int(re.search(r'shader == (0x[0-9a-f]+)ull', adapter)[1], 16)
    offset = 0
    while True:
        offset = blob.find(bytes.fromhex('0003feff'), offset)
        if offset < 0:
            return None
        tokens = blob[offset:offset + 320 * 4]
        if fnv(tokens) == pinned:
            return tokens
        offset += 4


def integration():
    header = (RT / 'rt_scene.h').read_text()
    assert 'uint64_t frame, view, target, target_generation;' in header
    source = (RT / 'rt_scene.m').read_text()
    render = (RT / 'render.m').read_text()
    present = (RT / 'render_present.m').read_text()
    d3d = (RT / 'd3d9.m').read_text()
    assert 'o->resource_id = ++resource_serial;' in d3d
    assert 'uint64_t resource_id;' in (RT / 'd3d9_internal.h').read_text()
    assert render.count('material_emit(&grass, &water, &aux, rt1, ^') == 4
    assert 'pse->refl.uses_kill?RT_SCENE_ALPHA:0' in render
    emission = render.split('static void material_emit(', 1)[1].split('// One metadata lookup', 1)[0]
    assert emission.count('rt_scene_capture(rt1)') == 1
    assert emission.index('if (ssao_replay)') < emission.index('rt_scene_capture(rt1)') < emission.index('if (glass_emit')
    assert 'if (rt1 && !q_active && !lamp_replay)' in emission
    boundary = present.index('rt_scene_flush(cmd)')
    assert present.rfind('end_pass();', 0, boundary) > present.rfind('msaa_resolve(&bb_rt)', 0, boundary)
    assert boundary < present.index('probe_present(cmd, backbuffer_tex)')
    assert 'rt_scene_forget(o->resource_id)' in render and 'rt_scene_reset()' in render
    assert 'temporal_' not in source and 'temporal_' not in (ROOT / 'recomp/private_runtime/game_rt_scene.h').read_text()
    manifest = json.loads((RT / 'runtime_sources.json').read_text())
    assert manifest['sources']['rt_scene.m']['targets'] == ['mw-private', 'mw-public', 'private']
    assert 'rt_scene.m' in manifest['targets']['private']['units']
    assert '    rt_scene.m \\' in (RT / 'build.sh').read_text()
    private = ROOT / 'recomp/private_runtime'
    feature = json.loads((private / 'features/rt_scene.json').read_text())
    assert feature['bindings']['game_rt_scene.h']['sha256'] == hashlib.sha256((private / 'game_rt_scene.h').read_bytes()).hexdigest()
    assert 'NFS_RT1\tdiag\toff\truntime/rt_scene.m\truntime/docs/rt_scene.md\tno' in (RT / 'switches.tsv').read_text()
    print('PASS integration: four base paths, shared registration, private camera binding, default Off', flush=True)


def main():
    private = ROOT / 'recomp/private_runtime'
    if not (private / 'game_rt_scene.h').exists() or not (private / 'features/rt_scene.json').exists():
        print('SKIP: private rigid adapter absent', flush=True)
        return 3
    integration()
    proof = []
    with tempfile.TemporaryDirectory(prefix='rt-scene-') as temp:
        out = Path(temp)
        env = dict(os.environ, NFS_RT1='0')
        compiler = ['clang', '-O1', '-fobjc-arc', '-I', RT, '-I', ROOT / 'recomp',
                    '-I', ROOT / 'recomp/private_runtime', '-framework', 'Foundation', '-framework', 'Metal',
                    '-framework', 'QuartzCore']
        cache = os.environ.get('NFS_METAL_CACHE')
        if cache:
            compiler += ['-fmodules-cache-path=' + cache]

        def build(name, game, mutation=False):
            binary = out / name
            flags = [f'-DNFS_GAME={game}']
            if mutation:
                text = (RT / 'rt_scene.m').read_text()
                original = 'memcpy(p->world,world,sizeof world);'
                assert text.count(original) == 1
                text = text.replace(original, 'static float frozen[16]; static int captured; '
                                    'if (!captured) { memcpy(frozen,world,sizeof frozen); captured=1; } '
                                    'memcpy(p->world,frozen,sizeof frozen);')
                mutant = out / 'frozen.m'
                mutant.write_text(text)
                flags += [f'-DRT_SCENE_SOURCE="{mutant}"']
            run([*compiler, *flags, RT / 'rt_scene_test.m', '-o', binary])
            return binary

        for game in (1, 2):
            good = build(f'off-{game}', game)
            report = run([good, 'off'], env=env).strip()
            proof.append(f'game={game} ' + report)
            print(proof[-1], flush=True)
        mw = RT.parent / 'host/shaders/testdata/world/t0_world_p0_vs_1_1.bin'
        private = private_shader()
        if not mw.exists() or private is None:
            print('SKIP: local rigid shader corpus absent', flush=True)
            return 3
        private_path = out / 'private-vs.bin'
        private_path.write_bytes(private)
        assert fnv(mw.read_bytes()) == 0x090160E8E9A32D2C
        gpu = not os.environ.get('CODEX_SANDBOX') or os.environ.get('NFS_GPU_SANDBOX') == '1'
        if not gpu:
            print('SKIP: GPU proof requires the offline GPU runner', flush=True)
            return 3
        env['NFS_RT1'] = '1'
        env['MTL_DEBUG_LAYER'] = '1'
        for game, shader in ((1, mw), (2, private_path)):
            good = build(f'good-{game}', game)
            frozen = build(f'frozen-{game}', game, mutation=True)
            paths = []
            for recorded in (0, 1):
                output = out / f'hits-{game}-{recorded}.bin'
                result = run([good, shader, recorded, output], env=env, allow_skip=True)
                assert 'second_wrong=0/256' in result and 'first_wrong=0/256 first_hits=16' in result, result
                print(result.strip(), flush=True)
                proof.append(result.strip())
                paths.append(output.read_bytes())
                result = run([frozen, shader, recorded, output], env=env, expected=1)
                assert 'second_wrong=16/256' in result and 'first_wrong=0/256 first_hits=16' in result, result
                print('FROZEN ' + result.strip(), flush=True)
                proof.append('FROZEN ' + result.strip())
            assert paths[0] == paths[1], 'direct/recorded hit bits differ'
        print('PASS RT1: both adapters, 0/256 wrong; frozen controls 16/256; direct/recorded readbacks identical', flush=True)
        artifact = ROOT / '.metal-cache/rt-scene-proof.txt'
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text('\n'.join(proof) + '\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Unavailable as error:
        print(str(error), flush=True)
        raise SystemExit(3)
