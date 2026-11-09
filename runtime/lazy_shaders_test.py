#!/usr/bin/env python3
"""














"""
from pathlib import Path
import hashlib
import re
import sys

ROOT = Path(__file__).resolve().parent

# (file, enclosing function): why a direct compile there never waits on the frame for an effect's first use.
REVIEWED = {
    ('render.m', 'render_init'): 'device creation, before pipeline_loading_done',
    ('render.m', 'shader_library'): 'translated game shaders: shader cache and worker threads, counted by shader_compile',
    ('render.m', 'float_scene_ramp'): 'diagnostic (BOOT_DIAG_FLOAT_SCENE_RAMP)',
    ('render.m', 'soft_proof_begin'): 'diagnostic (soft smoke proof)',
    ('render_post.m', 'render_contact_warm'): 'load: asynchronous compile on Metal\'s queue',
    ('render_post.m', 'x_contact_scene_impl'): 'diagnostic (legacy AO pair proof)',
    ('overlay.m', 'pso_init'): 'performance overlay, compiled when the player first opens it',
    ('envcube.m', 'envcube_pipeline'): 'non-default PC mip path and diagnostic compare',
    ('cliprec.mm', 'prepare:'): 'clip recorder start (player action, recorder thread)',
    ('sign_diag.m', '*'): 'diagnostic',
    ('hdrclip.m', '*'): 'diagnostic and its self-test',
}
# Effect-first-use helpers (each decoded to a first-use frame) and the load-time warm that must call each.
WARMED = {
    'render.m': ('render_lazy_warm', ['ssao_depth_warm', 'soft_depth_pipeline', 'soft_late_pipeline']),
    'render_post.m': ('render_post_lazy_warm', ['x_contact_smoke_multiply', 'x_contact_smoke_half_multiply', 'x_view_pso', 'raindrops_library', 'post_cuts_curve_pipeline']),
    'render_present.m': ('render_present_warm', ['gamma_library']),
}
OLD_SMOKE_SHA = '109d0fd826b03cab53120fd5fa3414ba2c7f3b0be72befd4c0c7c8eeabec50f8'
OLD_SMOKE_FRAGMENT = r'''static id<MTLRenderPipelineState> x_contact_smoke_multiply(MTLPixelFormat format) {
    static id<MTLRenderPipelineState> pso[3];unsigned n=format==MTLPixelFormatRGBA16Float?2:format==MTLPixelFormatBGRA8Unorm;
    if(!pso[n]){
        id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:nil];
        if(lib)pso[n]=pipeline_build(d, __func__, NULL);
    }
    return pso[n];
}
'''


def enclosing(lines, i):
    """The function whose definition starts last at column 0 above line i (signatures may span lines)."""
    for j in range(i, -1, -1):
        line = lines[j]
        if line[:1] in ('-', '+'):
            m = re.match(r'[-+]\s*\([^)]*\)\s*(\w+:?)', line)
            if m:
                return m.group(1)
        if re.match(r'[A-Za-z_]', line) and '(' in line and not line.rstrip().endswith(';'):
            m = re.search(r'(\w+)\s*\(', line)
            if m:
                return m.group(1)
    return '?'


def direct_compiles(name, text):
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        if 'newLibraryWithSource' not in line or line.lstrip().startswith('//'):
            continue
        func = enclosing(lines, i)
        if name == 'pipeline_cache.m' and func == 'pipeline_library_source':
            continue
        if (name, func) in REVIEWED or (name, '*') in REVIEWED:
            continue
        found.append(f'{name}:{i + 1} {func}')
    return found


def body(text, func):
    m = re.search(r'^(?:static\s+)?[\w<>]+[\s\*]+' + func + r'\([^)]*\)\s*\{', text, re.M)
    if not m:
        return ''
    end = text.index('\n}\n', m.end())
    return text[m.end():end]


def warm_schedules(texts):
    """
"""
    init = body(texts['render.m'], 'render_init')
    blocks = re.findall(r'dispatch_async\(shader_workers\[\d\],\s*\^\{(.*?)\}\);', init)
    counts = {}
    for _, (_, helpers) in WARMED.items():
        for h in helpers:
            counts[h] = 0
    for block in blocks:
        reached = ''
        for called in set(re.findall(r'\b(\w+)\(', block)):
            for text in texts.values():
                reached += body(text, called)
        for h in counts:
            counts[h] += bool(re.search(r'\b' + h + r'\(', reached))
    return counts


def racy_statics(text):
    """Function statics of pipeline_library_source assigned outside dispatch_once (it runs on four threads at once)."""
    b = body(text, 'pipeline_library_source')
    names = [n for t, n in re.findall(r'static\s+([\w<>]+)\s+(\w+)', b) if t != 'dispatch_once_t']
    once = ''.join(re.findall(r'dispatch_once\(&\w+,\s*\^\{(.*?)\}\);', b, re.S))
    outside = b
    for chunk in re.findall(r'dispatch_once\(&\w+,\s*\^\{.*?\}\);', b, re.S):
        outside = outside.replace(chunk, '')
    outside = re.sub(r'static\s+[\w<>]+\s+\w+\s*;', '', outside)
    return [n for n in names if re.search(r'\b' + n + r'\s*=(?!=)', outside)]


def main():
    sources = [p for p in sorted(ROOT.glob('*.m')) + sorted(ROOT.glob('*.mm'))
               if '_test' not in p.name and not p.name.endswith('_bench.m')]
    if not sources:
        print('SKIP: no runtime sources')
        return 3
    flagged = []
    for p in sources:
        flagged += direct_compiles(p.name, p.read_text())
    unwarmed = []
    for name, (warm, helpers) in WARMED.items():
        b = body((ROOT / name).read_text(), warm)
        unwarmed += [f'{name}:{warm} misses {h}' for h in helpers if not re.search(r'\b' + h + r'\(', b)]
    texts = {n: (ROOT / n).read_text() for n in ('render.m', 'render_post.m', 'render_present.m')}
    # Post effects stay prepared even when scene replay is explicitly disabled.
    def scene_guards(text):
        return body(text, 'render_post_prewarm').count('NFS_PSO_PREWARM')
    guards = scene_guards(texts['render_post.m'])
    guarded = texts['render_post.m'].replace('void render_post_prewarm(void) {',
        'void render_post_prewarm(void) {\n    if (getenv("NFS_PSO_PREWARM")) return;', 1)
    assert scene_guards(guarded) == 1
    print(f'post preparation unset/0: switch guards={guards}; gated control=1')
    schedules = warm_schedules(texts)
    twice = [f'{h} warmed by {n} concurrent load blocks' for h, n in sorted(schedules.items()) if n != 1]
    racy = [f'pipeline_library_source static {n} set outside dispatch_once'
            for n in racy_statics((ROOT / 'pipeline_cache.m').read_text())]
    for f in flagged + unwarmed + twice + racy:
        print('FAIL:', f)
    digest = hashlib.sha256(OLD_SMOKE_FRAGMENT.encode()).hexdigest()
    assert digest == OLD_SMOKE_SHA, digest
    control = direct_compiles('render_post.m', OLD_SMOKE_FRAGMENT)
    print(f'lazy_shaders_test: files={len(sources)} direct compiles outside the list={len(flagged)} '
          f'unwarmed helpers={len(unwarmed)} helpers not warmed exactly once={len(twice)} racy statics={len(racy)}; '
          f'pinned original smoke multiply flagged={len(control)}')
    if len(control) != 1:
        print('FAIL: the pinned original lazy compile was not flagged')
        return 1
    return 1 if flagged or unwarmed or twice or racy or guards else 0


if __name__ == '__main__':
    sys.exit(main())
