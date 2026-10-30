"""Native held-input post cuts, discovered by offline_tests.sh; no game/window.
Off shader source: pinned SHA-256 against main 1e992e08 before device discovery.
Observed GPU: final RGBA8 max=0, RGBA16F max=1 stored code; exact alpha at
1x/2x (33,177,600 final pixels). Equal-weight control fails: final max=68/542
codes respectively, exit 1 (normal exit 0). Curve max=0/1; broken=176/2048.
AO production entry: 6 same-frame depth preparations, 0 failures (exit 0),
13,824 identical On/Off R8 bytes. Serial-cache mutation: 1 preparation and
5 failures (exit 1) with cuts both On and Off; the old harness passed (exit 0).
Empty/incremental/changed-depth/proof/split paths use the production replay hook.
Split/partial targets preserve all stored channels and pixels outside the rectangle.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent

def section(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]

def run(args, **kw):
    p = subprocess.run([str(x) for x in args], capture_output=True, text=True, **kw)
    if p.returncode:
        raise RuntimeError((p.stdout+p.stderr)[-6000:])
    return p.stdout

def main():
    post = (ROOT/'render_post.m').read_text()
    shader = section(post, 'static const char *x360_src =', '#include "lampglow_shader.h"')
    assert hashlib.sha256(shader.encode()).hexdigest() == 'a7b0ddc42942bd19ebada259861bd6503c9253474d2a881130c1b3f0fe594146'
    print('Off legacy source byte-identical:', len(shader.encode()), 'source bytes', flush=True)
    if not (ROOT/'gen/x360_shader_lits.inc').is_file():
        print('SKIP: generated shader literals unavailable')
        return 3
    with tempfile.TemporaryDirectory(prefix='post-cuts-') as td:
        d = Path(td)
        generated = '#include "gen/x360_shader_lits.inc"\n'+shader
        generated += section(post, 'static NSString *x_edr_source(', '// END HDR output M post variant')
        (d/'shader.inc').write_text(generated)
        (d/'destination.inc').write_text(section(post, 'static void x_destination(', '// Crop before any filtered tap.'))
        render=(ROOT/'render.m').read_text()
        coverage=section(render, 'static const MTLSamplePosition ssao_samples[4]', 'static int ssao_prepare(')
        coverage+=section(render, 'static id<MTLRenderPipelineState> ssao_depth_pipeline;', 'id<MTLTexture> render_ssao_cpu_reference_coverage(')
        depth_encoder='id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];'
        assert coverage.count(depth_encoder)==1
        coverage=coverage.replace(depth_encoder,'observe_depth_prepare();'+depth_encoder)
        coverage=coverage.replace('enc=[cmd renderCommandEncoderWithDescriptor:rp];', 'observe_coverage(rp,allow_cuts);enc=[cmd renderCommandEncoderWithDescriptor:rp];')
        (d/'coverage.inc').write_text(coverage)
        action=next(line for line in post.splitlines() if 'loadAction = proof<4 || post_cuts_full_target' in line)
        (d/'load.inc').write_text('static MTLLoadAction final_load(int split,unsigned dl,unsigned dsl,unsigned w,unsigned h,id<MTLTexture> out) { int proof=4; void *view=split?(void*)1:NULL; MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];'+action+'return rp.colorAttachments[0].loadAction;}')
        exe=d/'test'
        compile_args=['clang','-O1','-g','-fobjc-arc','-I',ROOT,'-I',d,ROOT/'post_cuts_test.m',ROOT/'post_cuts.c',
             '-framework','Foundation','-framework','Metal','-o',exe]
        run(compile_args)
        for mode in (None, '', '0', 'invalid'):
            env = dict(os.environ)
            if mode is None: env.pop('NFS_POST_CUTS', None)
            else: env['NFS_POST_CUTS'] = mode
            run([exe, '--off'], env=env)
        p=subprocess.run([str(exe)],text=True,capture_output=True)
        proof = p.stdout+p.stderr
        print(p.stdout+p.stderr, end='', flush=True)
        if p.returncode == 3:
            return 3
        assert p.returncode == 0, p.returncode
        for on in ('0','1'):
            result=run([exe,'--coverage',d/('ao-'+on)],env={**os.environ,'NFS_POST_CUTS':on})
            proof+=result
            print(result,end='',flush=True)
        off=(d/'ao-0').read_bytes();on=(d/'ao-1').read_bytes()
        assert off==on, f'AO coverage differs in {sum(a!=b for a,b in zip(off,on))} bytes'
        proof+=f'AO coverage exact: {len(off)} bytes\n'
        # Compile the same extracted production path with only the serial cache
        # mutation. It must skip preparation on calls 2..6 in the same frame.
        original='if (!ssao_depth_prepare(depth)) return nil;'
        assert coverage.count(original)==1
        cached=coverage.replace(original,'if (ssao_half_serial!=frame_serial && !ssao_depth_prepare(depth)) return nil;')
        (d/'coverage.inc').write_text(cached)
        mutant=d/'cache-test'
        run(compile_args[:-1]+[mutant])
        for on in ('0','1'):
            p=subprocess.run([str(mutant),'--coverage',str(d/('cache-'+on))],
                             env={**os.environ,'NFS_POST_CUTS':on},text=True,capture_output=True)
            result=f'serial-cache control On={on} exit={p.returncode}: '+p.stdout+p.stderr
            print(result,end='',flush=True);proof+=result
            assert p.returncode==1, result
            assert 'depth_prepares=1 calls=6' in p.stdout, result
        p=subprocess.run([str(exe),'--broken'],text=True,capture_output=True)
        print('equal-weight control:', p.stdout+p.stderr,end='',flush=True)
        proof += 'equal-weight control: '+p.stdout+p.stderr
        artifact=ROOT.parent/'.metal-cache/post-cuts-proof.log'
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(proof)
        assert p.returncode == 1, p.returncode
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
