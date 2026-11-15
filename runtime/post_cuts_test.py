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
Exact new cuts at 3440x2160 2x: five held SDR + five held EDR frames,
74,304,000 pixels, changed=0 / max stored code=0 (exit 0).
14 downscale/kernel outputs, 12 production full/partial quad copies and four
mip/array-slice/partial/view cases (all subresources) are exact.
Wrong blur threshold (>0.25 instead of !=0): eight held frames differ,
SDR frame 1 changed=1,250,528 / max=53; EDR frame 1 changed=1,423,804 /
max half-bit code=14,006 (exit 1; eight equality failures).
Held game SDR: frames 179/473/894/1254/1674, all changed=0/max=0;
wrong threshold changes 1,225/7,003/397,581/477,276/505,403 pixels,
max=1/1/16/26/27. The decoder rejects these as production (exit 1 vs 0).

`post_cuts_test.py` holds production shader inputs at 3440×2160 with 2x AA: five stopped/slow/fast/DOF/deband cases in SDR and EDR, 74,304,000 pixels, zero changed pixels and zero maximum stored-code difference. Both downscale outputs, Gaussian/bloom kernels and full/partial production quad copies are also byte-equal. A deliberately wrong blur threshold (`amt > 0.25`) fails eight frame comparisons: SDR case 1 changes 1,250,528 pixels (maximum 53 codes), EDR case 1 changes 1,423,804 (maximum 14,006 stored half-bit codes). The older 3840×2160 curve check remains separate and allows one stored HDR code of rounding difference. Native proof details are saved to `.metal-cache/post-cuts-proof.log`.

The held game proof renders the legacy, wrong-threshold and production chains from the same scene, depth, vignette, parameters and exposure history before simulation resumes. The temporary replay harness is removed afterward; its patch and decoded proof remain under `.metal-cache/postcuts2-*`. Independent launches vary, so cross-launch screenshots alone cannot establish byte equality. `post_cuts_test.py --held-game <diag-directory>` checks the shared frame/clock/speed, decodes raw stored channels, requires zero production differences and rejects an undetected moving-frame control. Supplying the wrong-threshold images as production fails (exit 1; correct production exits 0). Quads separately retain every channel and untouched subresource in native 1x/2x/4x, mip, array-slice, partial and texture-view checks.

| Output/frame (sim seconds; m/s) | Byte-equal | Production changed pixels / max code | Broken changed pixels / max code | Exact-zero blur pixels / 7,430,400 |
|---|---|---|---|---|
| SDR 179 (0.116667; 0) | yes | 0 / 0 | 1,225 / 1 | 700,447 (9.4268%) |
| SDR 473 (5.016677; 0) | yes | 0 / 0 | 7,003 / 1 | 4 (0.000054%) |
| SDR 894 (12.033321; 31.474203) | yes | 0 / 0 | 397,581 / 16 | 562,316 (7.5678%) |
| SDR 1254 (18.033230; 61.762897) | yes | 0 / 0 | 477,276 / 26 | 638,353 (8.5911%) |
| SDR 1674 (25.033123; 75.886864) | yes | 0 / 0 | 505,403 / 27 | 930,250 (12.5195%) |
| HDR request 180 (0.116667; 0) | yes | 0 / 0 | 978 / 1 | 700,447 (9.4268%) |
| HDR request 474 (5.016677; 0) | yes | 0 / 0 | 6,233 / 1 | 4 (0.000054%) |
| HDR request 895 (12.033321; 31.474203) | yes | 0 / 0 | 503,885 / 16 | 562,316 (7.5678%) |
| HDR request 1255 (18.033230; 61.762897) | yes | 0 / 0 | 580,783 / 25 | 638,353 (8.5911%) |
| HDR request 1675 (25.033123; 75.886864) | yes | 0 / 0 | 591,028 / 27 | 930,250 (12.5195%) |

All ten held game frames (74,304,000 pixels) are byte-equal. The unattended HDR
request enabled linear RGBA16F transport (layer/drawable format 115), but the
display headroom was 1.0 at all five held captures: their treatment target was
BGRA8 (format 80), the SDR fallback. Headroom varied elsewhere in the run.
The five native EDR cases above cover the HDR shader/target separately; these
game captures do not establish byte equality for an H>1 game treatment frame.
The HDR baseline waited for a shared slot, then completed; no slot retry was
issued. Six total game runs used 580 requested seconds, including two initial
reference-recipe runs at the wrong resolution/AA, excluded from this proof.

Logged `BOOT_DIAG_GPUPASS=1` observations below are encoder counts per frame and milliseconds as printed, without a timing claim. SDR Off is the complete frames 1042–1102 block; On is frames 1025–1085, outside the held replay frames. HDR-request Off is frames 1021–1081; On is frames 1026–1086. Each block has 61 logged frames. The runs use `BOOT_RACE=15.1.1 BOOT_RACE_AI=0 BOOT_RACE_TRAFFIC=0 BOOT_AUTODRIVE=1 NFS_LOCKSTEP=1 NFS_SIMHZ=60 NFS_DISPLAY_PIXELS=3440x2160 BOOT_REG=g_RacingResolution=4,g_FSAALevel=1,g_ShadowDetail=2,g_RoadReflectionEnable=2,g_RainEnable=0 NFS_POSTFX=x360 BOOT_DIAG_SIMSHOTS=0.10,5,12,18,25`; `NFS_POST_CUTS=0/1` and `NFS_HDR_OUTPUT=off/edr` select the pair. Resolution index 0 selects 1144×720 on this display; AA level 2 selects 4x, so those reference-recipe values do not test this target.

| Pass | Encoders/frame | SDR Off ms | SDR On ms | HDR request Off ms | HDR request On ms |
|---|---|---|---|---|---|
| x360 vt bb 3440×2160 2x | 1.0 | 1.461 | 1.585 | 2.038 | 2.684 |
| x360 pass 860×540 | 2.0 | 0.291 | 0.222 | 0.498 | 0.456 |
| x360 pass 430×270 | 5.0 | 0.086 | 0.097 | 0.106 | 0.120 |
| x360 pass 287×180 | 1.0 | 0.126 | 0.144 | 0.278 | 0.157 |
| x360 pass 64×64 | 1.0 | 0.046 | 0.052 | 0.087 | 0.057 |
| quad rt 3440×2160 1x | 1.0 | 0.128 | 0.149 | 0.356 | 0.334 |
| quad rt 3440×2160 2x | 1.0 (HDR request only) | absent | absent | 0.230 | 0.251 |

The treatment and full-copy attachment labels change from `cL` to `cD`; all later stores remain. The switch stays Off pending a solo matrix measurement at the target resolution.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tempfile

import sys as _source_sys
_source_sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_file_fixture import mw_source

ROOT = Path(__file__).resolve().parent

def section(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]

def run(args, **kw):
    p = subprocess.run([str(x) for x in args], capture_output=True, text=True, **kw)
    if p.returncode:
        raise RuntimeError((p.stdout+p.stderr)[-6000:])
    return p.stdout

def held_game(directory):
    """Decode raw same-command-buffer legacy/broken/mask/production captures.

    The temporary replay patch is retained with the local proof artifacts, not
    in the renderer. Independent game launches are not byte-stable inputs.
    """
    import json
    import re
    import struct
    import numpy as np
    directory = Path(directory)
    records = []
    with (directory/'events.log').open() as stream:
        for line in stream:
            if 'postcuts-held index=' in line:
                records.append(dict(re.findall(r'(\w+)=([^ ]+)', line.strip())))
    result = []
    for index in range(5):
        events = [r for r in records if int(r['index']) == index]
        assert sorted(int(r['phase']) for r in events) == [0, 1, 2, 3], events
        frozen = [{k:v for k,v in r.items() if k != 'phase'} for r in events]
        assert all(r == frozen[0] for r in frozen), events
        assert frozen[0]['size'] == '3440x2160' and frozen[0]['samples'] == '2', events
        captures = []
        header = None
        for phase in (0, 1, 3):
            raw = (directory/f'postcuts-held-{index}-{phase}.bin').read_bytes()
            current = struct.unpack('<4I', raw[:16])
            if header is None: header = current
            assert current == header
            w,h,bpp,fmt = header
            assert len(raw) == 16+w*h*bpp and bpp in (4,8)
            captures.append(np.frombuffer(raw[16:], dtype='<u2' if bpp==8 else 'u1').reshape(h,w,4))
        mask = (directory/f'postcuts-held-{index}-2.bin').read_bytes()
        assert len(mask) == 24 and struct.unpack('<4I',mask[:16]) == header
        skipped = struct.unpack('<Q',mask[16:])[0]
        row = dict(index=index, frame=int(events[0]['frame']), sim=float(events[0]['sim']),
                   speed=float(events[0]['speed']), format=fmt, pixels=w*h,
                   skipped=skipped, skipped_fraction=skipped/(w*h))
        for mode, pixels in (('production', captures[2]), ('broken', captures[1])):
            delta = np.abs(pixels.astype(np.int32)-captures[0].astype(np.int32))
            changed = int(np.count_nonzero(np.any(delta,axis=2)))
            row[mode] = dict(byte_equal=changed==0,changed_pixels=changed,max_code=int(delta.max()))
        print(f"held game {index} frame={row['frame']} sim={row['sim']:.6f} speed={row['speed']:.6f} "
              f"format={fmt} production={row['production']} broken={row['broken']} "
              f"blur_skipped={skipped}/{w*h} ({row['skipped_fraction']:.6%})", flush=True)
        result.append(row)
    artifact = ROOT.parent/'.metal-cache'/f'postcuts2-{directory.parent.name}-proof.json'
    artifact.write_text(json.dumps(dict(directory=str(directory),frames=result),indent=2)+'\n')
    assert all(r['production']['byte_equal'] for r in result), 'production differs on held game input'
    assert all(r['broken']['changed_pixels'] for r in result if r['speed']>0), 'broken blur control was not detected'
    print('held game proof PASS; artifact:', artifact)
    return 0

def main():
    if len(sys.argv)==3 and sys.argv[1]=='--held-game':
        return held_game(sys.argv[2])
    post = mw_source(ROOT/'render_post.m')
    shader = section(post, 'static const char *x360_src =', '#include "lampglow_shader.h"')
    assert hashlib.sha256(shader.encode()).hexdigest() == 'a7b0ddc42942bd19ebada259861bd6503c9253474d2a881130c1b3f0fe594146'
    print('Off legacy source byte-identical:', len(shader.encode()), 'source bytes', flush=True)
    if not (ROOT/'gen/x360_shader_lits.inc').is_file():
        print('SKIP: generated shader literals unavailable')
        return 3
    with tempfile.TemporaryDirectory(prefix='post-cuts-') as td:
        d = Path(td)
        generated = '#include "gen/x360_shader_lits.inc"\n'+shader
        generated += section(post, 'static NSString *x_cuts_source(', '// Separate curve library:')
        generated += section(post, 'static NSString *x_edr_source(', '// END HDR output M post variant')
        generated += 'typedef struct { float off[16][4],wgt[16][4],p0[4]; } XK;\n'
        generated += section(post, 'static void x_gauss5(', '// what the bloom chain makes')
        (d/'shader.inc').write_text(generated)
        (d/'destination.inc').write_text(section(post, 'static void x_destination(', '// Crop before any filtered tap.'))
        render=mw_source(ROOT/'render.m')
        quad=section(render, 'void quad_into(', 'void render_stretch(')
        quad=quad.replace('id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];', 'observe_quad(rp); id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];')
        legacy_quad=quad.replace('void quad_into(', 'void quad_legacy(').replace('complete ? MTLLoadActionDontCare : MTLLoadActionLoad','MTLLoadActionLoad')
        (d/'quad.inc').write_text(section(render, 'static const char *blit_src =', '// Startup-only hidden EDR transport;')+quad+legacy_quad)
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
            # Unset is On; only "0" is Off.
            run([exe, '--off' if mode == '0' else '--on'], env=env)
        p=subprocess.run([str(exe)],text=True,capture_output=True)
        proof = p.stdout+p.stderr
        print(p.stdout+p.stderr, end='', flush=True)
        if p.returncode == 3:
            return 3
        assert p.returncode == 0, p.returncode
        for mode in ('--exact','--exact-broken'):
            p=subprocess.run([str(exe),mode],text=True,capture_output=True)
            result=mode+': '+p.stdout+p.stderr
            proof+=result
            print(result,end='',flush=True)
            assert p.returncode==(1 if mode.endswith('broken') else 0), result
            assert ('exact post cuts failures=8' if mode.endswith('broken') else 'exact post cuts failures=0') in p.stdout, result
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
