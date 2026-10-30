"""























































































"""
import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import collections
import statistics

ROOT = Path(__file__).resolve().parent.parent
RT = ROOT / 'runtime'


def off_report(events, gpu, settings):
    assert 'enc/frame' in gpu, 'no GPU pass report'
    assert 'soft smoke' not in gpu, 'Off submitted a soft smoke pass'
    assert not re.search(r'(?m)^.*\tgraphics\.soft_smoke\t', settings), 'obsolete F10 row present'
    draws = sum(int(a)-int(b) for a,b in re.findall(r'particles:.*?replaced draws (\d+) \(rain (\d+)\)', events))
    assert draws > 0, 'no original smoke shader draws; Off test was not exercised'
    return draws


def validate_off(diag):
    diag = Path(diag)
    draws = off_report(*( (diag / name).read_text() for name in ('events.log','gpupass.log','settings_rows.txt')))
    print(f'PASS native Off: {draws} non-rain particle shader draws reported, 0 soft smoke GPU passes/snapshots; no F10 row')


def cost(diag):
    diag=Path(diag);frames={};passes=collections.defaultdict(list);totals={}
    for line in (diag/'events.log').read_text().splitlines():
        m=re.search(r'soft smoke cost: frame (\d+) sim ([0-9.]+) on (\d) held 1',line)
        if m:frames[int(m[1])]=(float(m[2]),int(m[3]))
    for line in (diag/'gpusplit_cost.log').read_text().splitlines():
        m=re.match(r'F (\d+) E \d+ ([0-9.]+) (.*)',line)
        if m and int(m[1]) in frames:passes[int(m[1])].append((m[3],float(m[2])))
        m=re.match(r'F (\d+) total ([0-9.]+) cmdbuf ([0-9.]+)',line)
        if m:totals[int(m[1])]=float(m[2])
    groups=collections.defaultdict(list)
    for frame,(sim,on) in frames.items():
        if not on or frames.get(frame-1)!=(sim,0):continue
        assert frame in totals and frame-1 in totals, 'paired frame lacks GPU report'
        assert not any('soft smoke' in name for name,value in passes[frame-1]), 'Off submitted soft passes'
        main=lambda f:sum(name.startswith('game bb') for name,value in passes[f])
        assert main(frame)==main(frame-1), 'On split the main MSAA pass'
        delta=totals[frame]-totals[frame-1];groups[sim].append(delta)
        print(f'PAIR sim={sim:.6f} frames={frame-1}/{frame} GPU pass sum {totals[frame-1]:.3f}/{totals[frame]:.3f} delta={delta:+.3f} ms')
    assert groups, 'no held On/Off GPU pairs'
    for sim,values in groups.items():print(f'DIAG sim={sim:.6f} n={len(values)} median On-Off={statistics.median(values):+.3f} ms')
    values=[v for group in groups.values() for v in group]
    print(f'DIAG all {len(values)} pairs median On-Off={statistics.median(values):+.3f} ms; non-solo, not an FPS/timing acceptance')


def validate(diag, variant='on', jpeg=None):
    """Same-input, pre-rain/post/HUD scene images, never separate game ticks.

    Sharp jumps are central-difference gradient magnitudes >8/255 in the BT.709
    smoke contribution (image minus its exact opaque scene). Rectangles are fixed
    fractions of the image: road (.10,.63,.50,.86), cars (.42,.57,.95,.84).
    This is a contact-region contrast measure; the labelled crops establish which
    visible intersections it measures. It is not a count of all geometry edges.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    diag = Path(diag)
    originals = sorted(diag.glob('soft_pair_*_original.png'))
    late='soft smoke late proof:' in (diag/'events.log').read_text()
    assert originals, 'no same-input proof images (BOOT_DIAG_SOFT_SMOKE=1 and held softon required)'
    panels = []
    for path in originals:
        prefix = path.name.removesuffix('original.png')
        images = {v: Image.open(diag / (prefix + v + '.png')).convert('RGB') for v in ('scene', 'original', 'off', variant, 'broken')}
        arrays = {v: np.asarray(im, dtype=np.float32) for v, im in images.items()}
        reference=diag/(prefix+'reference.png')
        if reference.exists() and variant=='on':
            ref=np.asarray(Image.open(reference).convert('RGB'),dtype=np.float32)
            assert np.array_equal(arrays['on'],ref), f'{prefix} on-store resolve differs from old nearest-depth copy: {np.any(arrays["on"]!=ref,axis=2).sum()} pixels'
            print(f'PASS {prefix} on-store/old nearest copy: 0 differing pixels')
        a, b = arrays['original'], arrays['off']
        assert np.array_equal(a, b), f'{prefix} Off changed {(a != b).sum()} channels'
        changed = np.any(np.abs(arrays[variant] - a) > 3, axis=2).sum()
        assert changed >= 5000, f'{prefix} fade disabled/not drawn: only {changed} pixels change by >3 levels'
        assert np.array_equal(a, arrays['broken']), f'{prefix} disabled-fade shader changed original pixels'
        print(f'PASS {prefix} raw scene: {"1x fade-disabled controls" if late else "original/Off"} 0 changed pixels; broken 0; On >3-level changes {changed}')
        counts = {}
        for name, roi in (('road', (.10,.63,.50,.86)), ('cars', (.42,.57,.95,.84))):
            w, h = images['off'].size
            box = tuple(int(v * s) for v, s in zip(roi, (w,h,w,h)))
            x0,y0,x1,y1 = box
            for v in ('off', variant):
                r = arrays[v] - arrays['scene']
                l = r[...,0]*.2126 + r[...,1]*.7152 + r[...,2]*.0722
                gy,gx = np.gradient(l[y0:y1,x0:x1])
                counts[v] = int((np.hypot(gx,gy) > 8).sum())
            before, after = counts['off'], counts[variant]
            assert before > 100 and after < .99*before, f'{prefix} {name}: sharp smoke jumps {before} -> {after} (need >=1% decrease)'
            print(f'PASS {prefix} {name}: sharp smoke jumps {before} -> {after} ({100*(1-after/before):.2f}% fewer)')
            panels.append((prefix + name + f': {before} -> {after}', [images[v].crop(box).copy() for v in ('off', variant, 'broken')]))
    if jpeg:
        # A real crop montage; no regenerated pixels, only labelled/resized captures.
        font_path = '/System/Library/Fonts/Helvetica.ttc'
        font = ImageFont.truetype(font_path, 21)
        canvas = Image.new('RGB', (1800, 95 + 270*len(panels)), (24,24,27))
        d = ImageDraw.Draw(canvas)
        samples = set(re.findall(r'soft smoke: snapshot.*samples (\d+) nearest', (diag / 'events.log').read_text()))
        if late:samples=set(re.findall(r'soft smoke late proof:.*opaque samples (\d+)',(diag/'events.log').read_text()))
        assert len(samples) == 1, 'missing or mixed snapshot sample counts'
        d.text((15,8), f'Soft smoke | same particle commands, opaque colour and depth | {w}x{h} / {samples.pop()}x opaque'+(' / 1x particles' if late else ' MSAA'), font=font, fill='white')
        for i, label in enumerate(('Fade disabled (1x control)' if late else 'Original = Off (0 changed pixels)', 'Soft smoke On', 'Broken: depth fade disabled')):
            d.text((15+i*600,40), label, font=font, fill='white')
        for row, (label, crops) in enumerate(panels):
            y = 80 + row*270
            d.text((15,y), label, font=font, fill='white')
            for col, crop in enumerate(crops):
                thumb = ImageOps.contain(crop, (590,225), Image.Resampling.LANCZOS)
                canvas.paste(thumb, (col*600+5,y+30))
        d.text((15,canvas.height-24), ('Raw scene with particle/rain replay, before post and HUD.' if late else 'Raw scene before rain, post and HUD.')+' Contact contrast counts; no timing claim.', font=font, fill='white')
        jpeg = Path(jpeg)
        for quality in (90,85,80,75):
            canvas.save(jpeg, quality=quality, optimize=True)
            if jpeg.stat().st_size < 500000:
                break
        assert jpeg.stat().st_size < 500000
        print(f'JPEG {jpeg}: {canvas.width}x{canvas.height}, {jpeg.stat().st_size} bytes')


def run(args, **kw):
    r = subprocess.run(list(map(str, args)), capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f'exit {r.returncode}: {args[0]}\n{r.stdout[-2000:]}{r.stderr[-4000:]}')
    return r.stdout


def function(text, name):
    start = text.index(name + '(')
    start = text.rfind('\n', 0, start) + 1
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    # Functions extracted here have balanced braces, including inside literals.
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def cstring(text, name):
    return re.search(r'static const char ' + name + r'\[\] =.*?;\n', text, re.S)[0]


def builtin_checks():
    renderer = (RT / "render.m").read_text()
    with tempfile.TemporaryDirectory(prefix="soft-smoke-builtin-") as scratch:
        tmp = Path(scratch)
        # Project production registry ids, as builtin_features_test.py does;
        # execute the real loader with the old profile and a live control row.
        port = (RT / 'settings_port.c').read_text()
        declarations = dict(re.findall(r'Setting\s+(\w+)\s*=\s*\{\s*\.id\s*=\s*"([^"]+)"', port))
        names = re.findall(r'REG\((\w+)\)|settings_add\(&(\w+)\)', port)
        ids = [declarations[a or b] for a, b in names if (a or b) in declarations]
        assert len(ids) > 20 and 'graphics.particles' in ids
        assert 'graphics.soft_smoke' not in ids, 'obsolete id still registered'
        assert 'render_set_soft_smoke' not in port + renderer, 'unused live setter remains'
        stubs = (RT / 'settings_look_test.c').read_text()
        stubs = stubs[stubs.index('#include <stdio.h>'):stubs.index('static int failures;')]
        env = {k: v for k, v in os.environ.items() if k != 'NFS_SOFT_SMOKE'}
        for label, keys, expected in (('production', ids, 0), ('restored F10 row', ids + ['graphics.soft_smoke'], 1)):
            rows = '\n'.join(f'    settings_add(&(Setting){{.id="{key}", .type=SET_HEADING}});' for key in keys)
            harness = stubs + '''
int main(void) {
    app_pref_set("graphics.soft_smoke", "off");
''' + rows + '''
    Setting live = {.id="test.live", .type=SET_TOGGLE, .def=1};
    settings_add(&live); settings_init();
    int failures = settings_find("graphics.soft_smoke") != NULL;
    failures += settings_get("test.live") != 1;
    failures += strcmp(app_pref("graphics.soft_smoke"), "off") != 0;
    failures += getenv("NFS_SOFT_SMOKE") != NULL;
    printf("registry/profile failures=%d; live=%d\\n", failures, settings_get("test.live"));
    return failures;
}
'''
            (tmp / 'settings.c').write_text(harness)
            run(['clang', '-I', RT, '-I', ROOT / 'recomp', tmp / 'settings.c', RT / 'settings.c', '-o', tmp / 'settings'])
            result = subprocess.run([tmp / 'settings'], env=env, capture_output=True, text=True)
            assert result.returncode == expected and not result.stderr, result.stdout + result.stderr
            print(label, result.stdout.strip())
        selector = function(renderer, 'soft_smoke_on')
        mutant = selector.replace('!e || strcmp(e, "off")', 'e && !strcmp(e, "on")')
        assert mutant != selector
        for label, body, expected in (('production', selector, 0), ('Off-default mutant', mutant, 5)):
            (tmp / 'default.c').write_text(
                '#include <stdlib.h>\n#include <string.h>\n'
                'static int soft_smoke=-1, diag_on;\n'
                'static int postfx_look_variant(const char *v) {(void)v; return 0;}\n'
                + body + '\nint main(void) {return soft_smoke_on();}\n')
            run(['clang', '-Wall', '-Wextra', '-Werror', tmp / 'default.c', '-o', tmp / 'default'])
            failures = 0
            for value, wanted in ((None, 1), ('', 1), ('on', 1), ('off', 0), ('OFF', 1), ('garbage', 1), ('0', 1)):
                case_env = dict(env)
                if value is not None:
                    case_env['NFS_SOFT_SMOKE'] = value
                result = subprocess.run([tmp / 'default'], env=case_env, capture_output=True, text=True)
                assert result.returncode in (0, 1) and not result.stderr, result.stderr
                if value is None:
                    default = result.returncode
                failures += result.returncode != wanted
            assert failures == expected and default == (expected == 0), (label, failures, default)
            print(f'{label} selector: {failures}/7 failures; unset={default}')


def main():
    builtin_checks()
    events = 'particles: mode x360 replaced draws 100 (rain 1)'
    gpu = 'enc/frame 1.0 game'
    settings = 'World\tgraphics.particles\tX360'
    assert off_report(events, gpu, settings) == 99
    try:
        off_report(events, gpu+'\nsoft smoke depth nearest', settings)
    except AssertionError:
        print('PASS Off report check; CONTROL unexpected snapshot rejected')
    else:
        raise AssertionError('Off snapshot report accepted')
    try:
        off_report(events, gpu, settings+'\nWorld\tgraphics.soft_smoke\tOff')
    except AssertionError:
        print('PASS absent F10 row; CONTROL restored row report rejected')
    else:
        raise AssertionError('obsolete F10 row report accepted')
    if not (RT / 'gen/pc_shader_lits.inc').exists():
        print('SKIP: private generated particle shader literals absent')
        raise SystemExit(3)
    materials = (RT / 'materials.m').read_text()
    renderer = (RT / 'render.m').read_text()
    original = run(['git', 'show', 'HEAD:runtime/materials.m'], cwd=ROOT)
    # Alpha-to-coverage adds only a shared optional epilogue. Preserve the full
    # source-byte oracle for the particle body and its original discard branch.
    def particle_body(text):
        value = cstring(text, 'particles_msl')
        value = value.replace('SM_ALPHA_COVERAGE_MSL\n', '')
        value = value.replace('SM_ALPHA_COVERAGE_APPLY("o.c0.w")\n', '')
        return value.replace('uint alpha_to_coverage, pad1;', 'uint pad0, pad1;')
    assert cstring(materials, 'particles_msl').count('SM_ALPHA_COVERAGE_MSL\n') == 1
    assert cstring(materials, 'particles_msl').count('SM_ALPHA_COVERAGE_APPLY("o.c0.w")\n') == 1
    assert particle_body(materials) == particle_body(original), 'Off particle body changed'
    unexpected = materials.replace('o.c0.w, aux.alpha_ref', 'o.c0.x, aux.alpha_ref')
    assert particle_body(unexpected) != particle_body(original), 'particle body oracle lost its negative control'
    assert cstring(materials, 'particles_msl').find('smoke_fade') == -1
    look = (RT / 'settings.c').read_text().split('kLook[] = {', 1)[1].split('};', 1)[0]
    assert '"graphics.soft_smoke"' not in look
    assert re.search(r'\{"graphics.look_soft_smoke",\s*"off",\s*"off"\}', look)
    print('PASS Off particle body byte-identical with shared coverage epilogue; discard-channel control rejected')
    with tempfile.TemporaryDirectory(prefix='soft-smoke-') as tmp:
        tmp = Path(tmp)
        # Execute the real production shader patcher, rather than a Python copy.
        src = ('#include <stdint.h>\n#include <stdlib.h>\n#include <stdio.h>\n#include <string.h>\n'
               '#include "roots.h"\n#include "../host/shaders/sm_coverage.h"\n#include "gen/pc_shader_lits.inc"\ntypedef struct { int unused; } SmResult;\n')
        src += cstring(materials, 'particles_msl') + cstring(materials, 'soft_smoke_math')
        src += function(materials, 'edit') + '\n'
        src += 'char *mat_replace(const uint32_t *t,uint32_t n,const SmResult *r,int *id) { *id=1; return strdup(particles_msl); }\n'
        src += 'int mat_is_particle(int id) { return id==1; }\n'
        src += function(materials, 'mat_soft_smoke_shader') + '\n'
        src += function(materials, 'mat_soft_smoke_resolved_shader') + '\n'
        src += function(materials, 'mat_soft_smoke_late_shader') + '\n'
        src += ('int main(int argc,char **argv) { int id=0; char *s=argc>1?(strcmp(argv[1],"late")?mat_soft_smoke_resolved_shader(0,0,0,&id):mat_soft_smoke_late_shader(0,0,0,&id)):mat_soft_smoke_shader(0,0,0,&id);'
                'if(!s||id!=1)return 1; puts(s); free(s); return 0; }\n')
        (tmp / 'dump.c').write_text(src)
        run(['clang', '-I', RT, tmp / 'dump.c', '-o', tmp / 'dump'])
        shader = run([tmp / 'dump'])
        resolved_shader = run([tmp / 'dump', 'resolved'])
        late_shader=run([tmp/'dump','late'])
        # Actual scalar MSL functions compiled as C, including the near-camera rule.
        math = shader[shader.index('inline float smoke_view_z'):shader.index('fragment Out sm_main')]
        math = math.replace('inline float', 'float')
        prefix = '#include <math.h>\nstatic float saturate(float x) { return fminf(1,fmaxf(0,x)); }\n'
        for broken in (False, True):
            body = math
            if broken:
                body = body.replace('float fade = saturate((smoke_view_z(scene, zn, zf) - pz) / range);', 'float fade = 1.0f;')
                assert body != math
            file = tmp / ('broken.c' if broken else 'fade.c')
            file.write_text(prefix + body)
            lib = file.with_suffix('.dylib')
            run(['clang', '-dynamiclib', '-O1', file, '-o', lib])
            fade = ctypes.CDLL(str(lib)).smoke_fade
            fade.restype = ctypes.c_float
            fade.argtypes = [ctypes.c_float] * 5
            failures = 0
            checks = 0
            for zn, zf in ((.1, 1000), (.5, 10000), (1, 5000)):
                def raw(z):
                    return zf * (z - zn) / (z * (zf - zn))
                for z in (2.5, 8, 25, 60):
                    for gap in (0, .06, .15, .3, .45, .6, 1.2):
                        expected = min(1, gap / .6)
                        got = fade(raw(z + gap), raw(z), zn, zf, .6)
                        failures += abs(got - expected) > .005
                        checks += 1
                for scene, particle in ((1, raw(30)), (raw(zn + .3), raw(zn + .2))):
                    failures += fade(scene, particle, zn, zf, .6) != 1
                    checks += 1
            print(f'{"CONTROL" if broken else "PASS"} production fade: {failures}/{checks} failures')
            assert failures > 30 if broken else failures == 0
        metal = run(['xcrun', '--find', 'metal'], env={**os.environ, 'TMPDIR': os.environ.get('OT_TOOL_TMPDIR', os.environ.get('TMPDIR','/tmp/'))}).strip()
        cache = tmp / 'modules'
        for shadow in (0, 1):
            file = tmp / f'particle{shadow}.metal'
            file.write_text(f'#define MAT_SHADOW_HW {shadow}\n' + shader)
            run([metal, '-fmodules-cache-path=' + str(cache), '-c', file, '-o', file.with_suffix('.air')], env={**os.environ, 'TMPDIR': str(tmp) + '/'})
            file = tmp / f'resolved{shadow}.metal'
            file.write_text(f'#define MAT_SHADOW_HW {shadow}\n' + resolved_shader)
            run([metal, '-fmodules-cache-path=' + str(cache), '-c', file, '-o', file.with_suffix('.air')], env={**os.environ, 'TMPDIR': str(tmp) + '/'})
            file=tmp/f'late{shadow}.metal';file.write_text(f'#define MAT_SHADOW_HW {shadow}\n'+late_shader)
            run([metal,'-fmodules-cache-path='+str(cache),'-c',file,'-o',file.with_suffix('.air')],env={**os.environ,'TMPDIR':str(tmp)+'/'})
        # Evaluate the exact production snapshot source using the C compiler.
        (tmp / 'depth.c').write_text('#include <stdio.h>\n' + cstring(renderer, 'soft_depth_msl') + '\nint main(void){puts(soft_depth_msl);}\n')
        run(['clang', tmp / 'depth.c', '-o', tmp / 'depth'])
        depth = run([tmp / 'depth'])
        for ms in (0, 1):
            file = tmp / f'depth{ms}.metal'
            file.write_text(f'#define SMOKE_MSAA {ms}\n' + depth)
            run([metal, '-fmodules-cache-path=' + str(cache), '-c', file, '-o', file.with_suffix('.air')], env={**os.environ, 'TMPDIR': str(tmp) + '/'})
        (tmp / 'copy.c').write_text('#include <stdio.h>\n' + cstring(renderer, 'soft_copy_msl') + '\nint main(void){puts(soft_copy_msl);}\n')
        run(['clang', tmp / 'copy.c', '-o', tmp / 'copy'])
        copy = run([tmp / 'copy'])
        for ms in (0, 1):
            file = tmp / f'copy{ms}.metal'
            file.write_text(f'#define SMOKE_MSAA {ms}\n' + copy)
            run([metal, '-fmodules-cache-path=' + str(cache), '-c', file, '-o', file.with_suffix('.air')], env={**os.environ, 'TMPDIR': str(tmp) + '/'})
        print('PASS offline Metal: 6 particle variants (copy/resolved/late), 2 depth snapshots, 2 sample-preserving proof copies')
        # Execute the exact draw-selection block with command spies. Off must not
        # request a shader variant or open a depth pass, even for eligible smoke.
        block = renderer.split('    int smoke_draw = 0;', 1)[1].split('    int blend = rs[27]', 1)[0]
        selection = r'''
#include <stdint.h>
#include <stdio.h>
#define MAT_VARIANT_SOFT_SMOKE 0x104
#define MAT_VARIANT_SOFT_SMOKE_RESOLVED 0x105
typedef struct { int mat, pending, ok; } ShaderEntry;
static struct { uint32_t viewport[6]; } dev;
static struct { void *ms, *tex; } bb_rt, ad_rt;
static int diag_on, ssao_replay, lamp_replay, q_active, enabled, view_ok=1;
static int gpt_on;
static void soft_smoke_cost_note(int material) {(void)material;}
static int variants, snapshots;
static ShaderEntry replacement={1,0,1};
static int soft_smoke_on(void) {return enabled;}
static int soft_smoke_late_on(void) {return 0;}
static int mat_is_particle(int id) {return id==1;}
static int mat_soft_particle_view(void) {return view_ok;}
static void diag_event(const char *fmt,...) {(void)fmt;}
static ShaderEntry *get_shader_variant(void *ps,void *po,int v) {++variants;return &replacement;}
static int soft_smoke_snapshot(void *z) {++snapshots;return 1;}
static int select_draw(uint32_t *rs,void *rt0,void *dst) {
    ShaderEntry base={1,0,1},*pse=&base;void *ps=0;int po=0;
    int smoke_draw=0;
''' + block + r'''
    return smoke_draw;
}
int main(void) {
    uint32_t rs[256]={0}; rs[7]=1;rs[27]=1;rs[19]=5;rs[20]=6;
    dev.viewport[5]=0x3F800000u;bb_rt.tex=(void*)1;ad_rt.tex=(void*)2;
    for(int i=0;i<1000;++i) if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 1;
    if(variants||snapshots)return 2;
    enabled=1;
    if(!select_draw(rs,bb_rt.tex,ad_rt.tex)||variants!=1||snapshots!=1)return 3;
    view_ok=0;if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 4;view_ok=1;
    rs[20]=2;if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 5;rs[20]=6;
    rs[14]=1;if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 6;rs[14]=0;
    q_active=1;if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 7;q_active=0;
    if(select_draw(rs,(void*)3,ad_rt.tex)||select_draw(rs,bb_rt.tex,(void*)4))return 8;
    ssao_replay=1;if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 9;ssao_replay=0;
    lamp_replay=1;if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 10;lamp_replay=0;
    dev.viewport[5]=0;if(select_draw(rs,bb_rt.tex,ad_rt.tex))return 11;
    if(variants!=1||snapshots!=1)return 12;
    puts("PASS actual draw selection: Off 1000 draws / 0 variant lookups / 0 snapshots; 9 excluded states");
    return 0;
}
'''
        (tmp / 'select.c').write_text(selection)
        run(['clang', '-Wno-incompatible-pointer-types', tmp / 'select.c', '-o', tmp / 'select'])
        print(run([tmp / 'select']).strip())
        mutated = selection.replace('return enabled;', 'return 1;')
        assert mutated != selection
        (tmp / 'select.c').write_text(mutated)
        run(['clang', '-Wno-incompatible-pointer-types', tmp / 'select.c', '-o', tmp / 'select'])
        negative = subprocess.run([tmp / 'select'], capture_output=True, text=True)
        assert negative.returncode == 1, negative.stdout + negative.stderr
        print('CONTROL actual draw selection: bypassed Off gate rejected (exit 1)')


if __name__ == '__main__':
    if len(sys.argv)>2 and sys.argv[1]=='--cost':
        cost(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == '--validate-off':
        validate_off(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] in ('--validate', '--validate-broken'):
        validate(sys.argv[2], 'broken' if sys.argv[1] == '--validate-broken' else 'on', sys.argv[3] if len(sys.argv)>3 else None)
    else:
        main()
