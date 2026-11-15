"""Authored lens drops: production generation, COM upload and Metal drop pass, no game assets.
Production: 0 non-finite; suppressed: 0 changed (exit 1).
Visibility, mean |drops - no drops| inside the two cap discs of size-3 drops
(limit .05): .098313; round 2's pupil-averaged ray field (drops a player could
not see) .020099; suppressed .000071. The earlier .1 limit rested on a doubled
ray scale (a 0.5 mm reference cap's deflection applied to 1 mm quad units).
Brightness, inside/ring luma over the same discs without drops, on a frame with
a vignetted border (limit .93): .806340 at 063ddc17 -> .957901.
Rim step, steepest neighbour-pixel step across the drop edge over the frame
value (limit .06): .012207 for the defocused edge of b168e2cc, .022786 at
063ddc17 -> .107422 (signed-distance edge, 1.5 px, lit-glass rim shading).
Sharp coverage mass by variant: 156.596/185.922/233.208/293.961 texels (area ratio 2^(1/3)).
Default (NFS_RAINDROPS unset, no console textures) must pick the authored drops:
production 2 (authored), unset-means-pc old rule (authored only for =x360) 0, exit 1.
Console shader digest and 18,432 legacy instance bytes remain identical.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'runtime'
LEGACY_LOOP = '    for (int i = 0; i < n; ++i) {\n        float rr = X360E_DROP_RADIUS * d[i].size, x0 = d[i].x * 2.0f - 1.0f, y0 = 1.0f - d[i].y * 2.0f;\n        q[i].rect[0] = x0; q[i].rect[1] = y0; q[i].rect[2] = x0 + rr / aspect * 2.0f; q[i].rect[3] = y0 + rr * 2.0f;\n        q[i].lc[0] = d[i].x - rr; q[i].lc[1] = d[i].y + rr; q[i].lc[2] = rr; q[i].lc[3] = -rr;\n        q[i].ba[0] = d[i].life * X360E_DROP_LIFE; q[i].ba[1] = (float)(d[i].variant & 3) * 0.25f; q[i].ba[2] = signature ? 1.0f : 0.0f; q[i].ba[3] = 0;\n    }\n'
LEGACY_LOOP_SHA = '98015857a99b8a8700e1f6d5fbb98cd923fe680d5b84a3ed450a9e06f6d0fc0c'


def run(cmd):
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError((p.stdout + p.stderr)[-6000:])
    return p.stdout

with tempfile.TemporaryDirectory(prefix='lens-drops-') as td:
    t = Path(td)
    source = (R / 'render_post.m').read_text()
    # Compile the complete production drop shader, pipeline and draw implementation.
    block = source[source.index('static const char *drops_src ='):source.index('// Post libraries first used')]
    shader = block.split('static const char *drops_src =',1)[1].split(';\n',1)[0]
    assert hashlib.sha256(shader.encode()).hexdigest() == '0aec71c2cae1e393ad23adf869ece1116336e7dd0aee03f86dcd841d155218b0', 'legacy drop shader source changed'
    (t / 'drops.inc').write_text(block)
    assert hashlib.sha256(LEGACY_LOOP.encode()).hexdigest() == LEGACY_LOOP_SHA
    instance = block.split('    struct { float rect[4], lc[4], ba[4]; } q[64];', 1)[1]
    instance = '    for (int i = 0; i < n; ++i) {' + instance.split('    for (int i = 0; i < n; ++i) {', 1)[1].split('    MTLRenderPassDescriptor *rp', 1)[0]
    (t / 'instances.inc').write_text('typedef struct {float rect[4],lc[4],ba[4];} Instance;\n'
        + 'static void original_instances(Instance *q,const RainDrop *d,int n,float aspect,int signature) {\n' + LEGACY_LOOP + '}\n'
        + 'static void production_instances(Instance *q,const RainDrop *d,int n,float aspect,int signature,int authored) {\n' + instance + '}\n')

    gen = (R / 'lens_drops.c').read_text()
    proof = []
    for mode in ('production', 'suppressed', 'old_default'):
        broken = mode != 'production'
        text = gen.replace('lens_drops_generate(&pixels);', 'memset(&pixels, 0, sizeof pixels);') if mode == 'suppressed' else gen
        if mode == 'old_default':   # authored drops only when NFS_RAINDROPS=x360 is explicit
            old = 'return env && !strcmp(env, "pc_before") ? 0 : env && !strcmp(env, "pc") ? 1 : 2;'
            assert old in text
            text = text.replace(old, 'return env && !strcmp(env, "x360") ? 2 : env && !strcmp(env, "pc_before") ? 0 : 1;')
        (t / 'generator.c').write_text(text)
        exe = t / mode
        run(['clang', '-O1', '-g', '-fobjc-arc', '-I', R, '-I', ROOT/'recomp', '-I', t,
             R/'lens_drops_test.m', t/'generator.c', '-framework', 'Foundation', '-framework', 'Metal',
             '-framework', 'QuartzCore', '-framework', 'ImageIO', '-framework', 'CoreServices', '-o', exe])
        p = subprocess.run([str(exe)], capture_output=True, text=True)
        line = mode + ': ' + (p.stdout+p.stderr).strip()
        print(line)
        proof.append(line)
        if p.returncode == 3:
            raise SystemExit(3)
        if mode == 'old_default':   # stops at the default-provider check (later steps need the authored textures)
            assert p.returncode != 0 and 'lens_drops_mode(NULL)==2)==2' in p.stderr, 'old default must fail the default check'
            continue
        measured = re.search(r'changed=(\d+) nonfinite=(\d+)', p.stdout)
        assert measured, 'missing pixel readback evidence'
        changed, nonfinite = map(int, measured.groups())
        assert nonfinite == 0 and (changed == 0 if broken else changed > 0), line
        seen = re.search(r'visibility=([\d.]+)', p.stdout)
        assert seen, 'missing visibility evidence'
        assert (float(seen.group(1)) < .05) if broken else (float(seen.group(1)) > .05), line
        bright = re.search(r'brightness=([\d.]+)', p.stdout)
        assert bright, 'missing brightness evidence'
        step = re.search(r'rim_step=([\d.]+)', p.stdout)
        assert step, 'missing rim evidence'
        if mode == 'production':
            assert float(bright.group(1)) >= .93 and float(step.group(1)) >= .06, line
        assert p.returncode == (0 if mode == 'production' else 1), 'broken control must fail the production check'

    (ROOT / '.metal-cache/lens-drops-proof.txt').write_text('\n'.join(proof)+'\n')
