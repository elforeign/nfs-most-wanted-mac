#!/usr/bin/env python3
"""Metal proof for shared_depth.m and render_post.m's shipping AO shader.
Pinned reference is the original four-tap AO, not the older 16-tap diagnostic.
The companion Metal fixture reports positive and deliberately broken runs;
each negative must return 1 with numerical failures (a crash/skip cannot pass).
Original entry point: shipping coverage 0/36, live bridge 0/8 (2 failures);
fixed entry point: 36/36 and 8/8 (0 failures). SP binding fails 2/18 checks;
fixed binding fails 0/18. Original fragments are SHA-256 pinned below.
No private assets, guest emulation, game or history reads.
Production graph hook: 108 AO/smoke cases (SP/P0/P1, x1/x2/x4/x8,
RGBA/BGRA/HDR), 0 unequal stored codes / 0 failures. Omitted dispatch:
4,893,908 unequal codes / 176 failures; skipped AO: same codes / 133 failures.
AO-Off registered normals/minmax: old request path 42 checks / 13 failures,
0/6 ready; fixed 60 / 0, 6/6 ready. Empty/Off/mirror paths: 0 Metal calls.
"""
from pathlib import Path
import ast
import hashlib
import os
import re
import subprocess
import tempfile

import sys as _source_sys
_source_sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_file_fixture import mw_source

ROOT = Path(__file__).resolve().parents[1]


def strings(body):
    return ''.join(ast.literal_eval(m[0]) for m in re.finditer(r'"(?:[^"\\]|\\.)*"', body))


def run(command):
    p = subprocess.run([str(x) for x in command], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError((p.stdout + p.stderr)[-5000:])
    return p.stdout


def function(source, name):
    """Extract a shipping C/ObjC function, never a handwritten encoder copy."""
    hit = re.search(r'^(?:static )?(?:void|int|id<MTLRenderPipelineState>|id<MTLTexture>) '
                    + name + r'\([^;]*?\)\s*\{', source, re.M | re.S)
    assert hit, name
    end, depth = hit.end(), 1
    for token in re.finditer(r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/|[{}]',
                             source[end:], re.S):
        if token[0] == '{':
            depth += 1
        elif token[0] == '}':
            depth -= 1
        if not depth:
            return source[hit.start():end + token.end()]
    raise AssertionError(name)


reference = (ROOT / 'runtime/shared_depth_reference.h').read_text()


def reference_fragment(name, digest):
    fragment = strings(reference.split('static const char ' + name + '[] =', 1)[1].split(';\n', 1)[0])
    assert hashlib.sha256(fragment.encode()).hexdigest() == digest, name
    return fragment


baseline = reference_fragment('reference_ao_msl', '5fe3297c47f72dd7b6ce6fd9be50e1b544fea1788352ab93e37909b5ae57a565')
descriptor = reference_fragment('reference_multiply_descriptor', '7107f3edf7d9d6b2f5149e89c92cd0a78eb4aaa7b441d05da5dbed61ec2d7ee3')
old_main = reference_fragment('reference_fixture_main', '07aaf5995f7d3ecd42b0c415a13f42a3c99c4fc9c161f7fd936cbe77308b186b')
old_player = reference_fragment('reference_player_binding', 'db19553c47afd09f00235c349dc3c8b13486afe358b896068723318cb873f4b6')
post = mw_source(ROOT / 'runtime/render_post.m')
target = (ROOT / 'runtime/render_target.m').read_text()
internal = (ROOT / 'runtime/render_internal.h').read_text()
current = post.split('static const char *ssao_src =', 1)[1].split(';\n', 1)[0]
# The pre-graph shader/encoders are unchanged fragments, pinned independently
# of the new dispatcher. Graph vs direct encoding must be exactly equal even
# in HDR (the older pre-G3 shader comparison allows one half-code step).
assert hashlib.sha256(strings(current).encode()).hexdigest() == 'b1871b4fc455b8d9edb6c3ba7d0e7ddb4dec49cd360e610866c913ea87659d0f'
for name, digest in [
        ('x_contact_scene_impl', 'aef5fdabc06e36905e1a9dbe6c09de5e0ad43f6a17106e6af5617494579d9ba7'),
        ('x_contact_smoke_multiply', '1b3dc082f9f90f7d7ca93b2279006588f23933d4970aaf0d2cabe3ea80edfa14'),
        ('x_contact_smoke_half_multiply', 'f152e7d641c50b62816363a335a1c3dac8ff293808910166e14e7fc52dbedb3b')]:
    assert hashlib.sha256(function(post, name).encode()).hexdigest() == digest, name
# The fixture compiles the current shipping fragment unchanged, with its real
# texture slots. The original baseline is pinned independently above.
assert 'geometry.read' in current and 'ao_normal(' not in current
assert post.count('scene=x_pre_treatment_dispatch(scene,depth_tex,') == 1

with tempfile.TemporaryDirectory(prefix='shared-depth-') as temporary:
    tmp = Path(temporary)
    # Only the public fullscreen vertex is needed from the larger VT library.
    vertex = strings(post.split('static const char *x360_src =', 1)[1].split('"struct K ', 1)[0])
    vertex = vertex[vertex.index('struct VOut'):]
    types = internal[internal.index('typedef struct {'):internal.index('} RenderTex;') + len('} RenderTex;')]
    contact = re.search(r'static struct \{ id<MTLRenderPipelineState> kernel,blur,apply,.*?contact_pso;', post)[0]
    ao_params = re.search(r'typedef struct \{ float proj\[4\].*?\} AOParams;', post)[0]
    (tmp / 'shared_depth_current.h').write_text(
        'static const char current_ao_msl[] =\n' + current.strip() + ';\n' +
        'static const char *ssao_src=current_ao_msl;\n' +
        'const char soft_half_up_msl[] =\n' + (ROOT / 'runtime/soft_smoke_half.m').read_text().split(
            'const char soft_half_up_msl[] =', 1)[1].split(';\n', 1)[0].strip() + ';\n' +
        'static const char *x360_src = "#include <metal_stdlib>\\nusing namespace metal;\\n"\n' +
        '"' + vertex.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '";\n' +
        'static const char *ssao_legacy_src=reference_ao_msl;\n' +
        types + '\n' + ao_params + '\n' + contact + '\nstatic int contact_state=2;\n')
    production = '\n'.join([function(internal, 'is_depth_pf'), function(target, 'reads_roll'),
                             function(target, 'msaa_resolve'), function(post, 'x_contact_multiply'),
                             function(post, 'x_contact_smoke_multiply'), function(post, 'x_contact_smoke_half_multiply'),
                             function(post, 'x_contact_scene_impl'), function(post, 'x_contact_scene'),
                             function(post, 'x_ao_smoke_node')])
    production += '\n' + re.search(r'^static PreTreatmentGraph x_pre_treatment=.*?}}};', post, re.M | re.S)[0]
    production += '\n' + function(post, 'render_pre_treatment_register')
    production += '\n' + function(post, 'x_pre_treatment_dispatch')
    hook = re.search(r'^    scene=x_pre_treatment_dispatch\(scene,depth_tex,.*?;', post, re.M)[0]
    production += r'''
static id<MTLTexture> production_pre_treatment(id<MTLTexture> scene,RenderTex *depth_rt,unsigned x_edr_processing,
    const X360PostParams *p,const X360PostView *view,unsigned x_mode_generation) {
    id<MTLTexture> depth_tex=depth_rt->tex;
''' + hook + '\n    return scene;\n}\n'
    modes = [('', ''), ('flipped', 'SHARED_DEPTH_TEST_FLIP_NORMALS'),
             ('skipped', 'SHARED_DEPTH_TEST_SKIP_LEVEL'), ('resize', 'SHARED_DEPTH_TEST_NO_RESIZE_RESET'),
             ('resolve', ''), ('multiply-sdr', ''), ('multiply-hdr', ''),
             ('gather-camera', ''), ('gather-msaa', ''), ('gather-hdr', ''),
             ('gather-player', ''), ('hook-msaa', ''), ('hook-hdr', ''),
             ('old-player', ''), ('old-harness', ''), ('graph-skipped', ''),
             ('graph-inputs', ''), ('graph-hook', '')]
    proof = []
    proof_path = ROOT / '.metal-cache/shared-depth-proof.log'
    proof_path.parent.mkdir(exist_ok=True)
    for name, define in modes:
        actual = production
        module = (ROOT / 'runtime/shared_depth.m').read_text()
        fixture = (ROOT / 'runtime/shared_depth_test.m').read_text()
        if name == 'graph-skipped':
            actual = actual.replace('scene=x_contact_scene(scene,frame->depth,frame->params,frame->view);', '(void)frame;(void)x_contact_scene;')
        if name == 'graph-inputs':
            actual = actual.replace('shared_depth_request(slot,frame_serial);', '(void)slot;')
        if name == 'graph-hook':
            # Original fixture bridge stopped after geometry preparation, so
            # it could not detect a missing production graph-dispatch hook.
            actual = actual.replace(hook, hook.replace('scene=x_pre_treatment_dispatch(', 'render_shared_depth_pre_treatment('))
            actual += '\nstatic void keep_dispatch(void) __attribute__((unused));\nstatic void keep_dispatch(void) { (void)x_pre_treatment_dispatch; }\n'
        if name == 'resolve':
            actual = actual.replace('a.resolveTexture = r->tex;', 'a.resolveTexture = r->tex;\n'
                                    '    if (is_depth_pf(r->ms.pixelFormat)) rp.depthAttachment.depthResolveFilter=MTLMultisampleDepthResolveFilterMin;')
        if name.startswith('multiply-'):
            fmt = 'MTLPixelFormatRGBA16Float' if name.endswith('hdr') else 'MTLPixelFormatBGRA8Unorm'
            actual = actual.replace('d.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorSourceAlpha;',
                                    'd.colorAttachments[0].destinationRGBBlendFactor=format==' + fmt + '?MTLBlendFactorOne:MTLBlendFactorSourceAlpha;')
        if name == 'hook-msaa':
            actual = actual.replace('depth_rt&&depth_rt->ms?(unsigned)depth_rt->ms.sampleCount:1', '1')
        if name == 'hook-hdr':
            actual = actual.replace('1,x_edr_processing,p,view,x_mode_generation);', '1,0,p,view,x_mode_generation);')
            actual = actual.replace('id<MTLTexture> depth_tex=depth_rt->tex;',
                                    '(void)x_edr_processing;id<MTLTexture> depth_tex=depth_rt->tex;')
        for field, old, broken in [('camera', 'in.camera=M32(view?view->source_view+0x40:0x00919690u);', 'in.camera=0;'),
                                   ('msaa', 'in.samples=samples;', 'in.samples=1;(void)samples;'),
                                   ('hdr', 'in.hdr=hdr;', 'in.hdr=0;(void)hdr;'),
                                   ('player', 'in.player=view_player(slot);', 'in.player=0;(void)view_player;')]:
            if name == 'gather-' + field:
                assert old in module
                module = module.replace(old, broken)
        if name == 'old-player':
            module = module.replace('in.player=view_player(slot);', '(void)view_player;' + old_player)
        if name == 'old-harness':
            # Compile the authenticated original entry point against the same
            # helpers; independent coverage assertions must reject its omissions.
            fixture = fixture.replace(function(fixture, 'main'), old_main.replace('int main(', 'static int original_main(') + r'''
int main(int argc,char **argv) {
    (void)check_bridge;(void)check_production;(void)check_graph_inputs;
    int rc=original_main(argc,argv);if (rc) return rc;
    check_production_coverage();
    printf("shared_depth original coverage: failures=%u shipping=%u/36 bridge=%u/8\n",failures,production_cases,bridge_cases);
    return failures?1:0;
}
''')
        if name and not define:
            assert actual != production or module != (ROOT / 'runtime/shared_depth.m').read_text() or \
                fixture != (ROOT / 'runtime/shared_depth_test.m').read_text(), name
        (tmp / 'shared_depth_production.h').write_text(
            descriptor.replace('x_contact_multiply(', 'reference_contact_multiply(') + '\n' + actual)
        (tmp / 'shared_depth.m').write_text(module)
        (tmp / 'shared_depth_test.m').write_text(fixture)
        exe = tmp / ('fixture-' + (name or 'normal'))
        command = ['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-DSHARED_DEPTH_TEST',
                   '-I', tmp, '-I', ROOT / 'runtime', '-I', ROOT / 'recomp']
        if define:
            command.append('-D' + define)
        command += [tmp / 'shared_depth_test.m', tmp / 'shared_depth.m', ROOT / 'runtime/pre_treatment.m',
                    '-framework', 'Metal', '-framework', 'Foundation', '-o', exe]
        run(command)
        arguments = [str(exe)] + (['--lifetime'] if name == 'resize' else
                                 ['--player'] if name == 'old-player' else
                                 ['--graph-inputs'] if name == 'graph-inputs' else
                                 ['--bridge'] if name.startswith(('gather-', 'hook-')) else
                                 ['--production'] if name in ('resolve', 'multiply-sdr', 'multiply-hdr') else [])
        p = subprocess.run(arguments, capture_output=True, text=True,
                           env={**os.environ, 'MTL_DEBUG_LAYER': '1'})
        result = (name or 'production') + ': ' + (p.stdout + p.stderr).strip()
        proof.append(result)
        proof_path.write_text('\n'.join(proof) + '\n')
        print(result, flush=True)
        if not name and p.returncode == 3:
            raise SystemExit(3)
        assert p.returncode == (1 if name else 0), (name, p.returncode)
        assert re.search(r'failures=([1-9]\d*)', p.stdout) if name else 'failures=0' in p.stdout
        if name == 'old-harness':
            assert 'failures=2 shipping=0/36 bridge=0/8' in p.stdout
        if name == 'old-player':
            assert 'checks=18 failures=2' in p.stdout
print('shared_depth_test: PASS (shipping resolve/multiply/bridge and broken controls rejected)')
