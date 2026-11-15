"""Production lacquer regression, using digest-pinned shipped helper/call fragments.

Round-1 -> fixed: highlight failures 48/96 -> 0/96, minimum On/Off
.16666667 -> .92418188; grazing failures 96/96 -> 0/96, minimum ratio
.60687029 -> 1.36829150. Diffuse failures 96/96 -> 0/96; transmission
60/69 -> 0/69. See the Metal header for decomposed character-line terms.
Unit-radiance layer max 1; inherited HDR paint max 6.95199966, bounded
by max(1,Off) instead of normalizing the existing hotspot/reflection.
Good/lobe/attenuation/env layer failures 0/7/23/13 of 23; all broken
controls exit 1. Smooth reflection good 0/192, rough control 96/192;
t0 dispatch old/new 0/2 -> 2/2, native link, HDR/gating 0/768 failures.
Off 13427 bytes identical, 20 variants, 1536 pixels, max error 4.08e-6,
flake changes 144/528 channels, nonfinite 0. No assets/device: exit 3.
Never launches a game.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import sys as _source_sys
_source_sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_file_fixture import mw_source

# Minimal source fragments from the reviewed baseline; independent of history.
ROUND1_MATH = 'static float3 coat_unit(float3 x) {\n    float q = dot(x, x);\n    return q > 1e-20f ? x * rsqrt(q) : float3(0, 0, 1);\n}\nstatic float coat_fresnel(float cosine) {\n    float t = 1.0f - saturate(cosine), t2 = t * t;\n    return 0.04f + 0.96f * t2 * t2 * t;\n}\nstatic float3 coat_weights(float3 normal, float3 eye, float3 light, float visible) {\n    float3 N = coat_unit(normal), V = coat_unit(eye), L = coat_unit(light);\n    float nv = saturate(dot(N, V)), nl = saturate(dot(N, L));\n    float F = coat_fresnel(nv);\n    float rv = saturate(dot(reflect(-V, N), L));\n    float peak = powr(rv, 256.0f) * float(nv > 0.0f && nl > 0.0f);\n    float direct = min(F, coat_fresnel(dot(V, coat_unit(V + L))) * peak) * saturate(visible);\n    return float3(1.0f - F, F - direct, direct);\n}\nstatic float3 coat_layer(float3 base, float3 env, float3 sun, float3 N, float3 V, float3 L, float visible) {\n    float3 w = coat_weights(N, V, L, visible);\n    base = max(base, 0.0f);\n    base /= max(1.0f, max(base.r, max(base.g, base.b)));\n    return w.x * base + w.y * env + w.z * sun;\n}\n'
assert hashlib.sha256(ROUND1_MATH.encode()).hexdigest() == '30b7e1e974418310dca7b7010cf16e371109d0a142d30b754ef868fbcf71005a'
ROUND1_CALLS = '        o.c0.rgb = coat_layer((v0.rgb * tex.rgb + s) * shade, env.rgb * saturate(v1.w * v0.w * 0.5f * (1.0f + 2.0f * m.k[2].x * v0.w)), m.k[1].rgb * v0.w, pi.texcoord4.xyz, v, l, lit);\n            o.c0.rgb = coat_layer((D * dif * tex.rgb + s2) * shade, tex1.sample(smp1, float3(-Vt.xy, Vt.z)).rgb * saturate(2.0f * fres * v0.w * pi.texcoord6.w), m.k[1].rgb * v0.w, N, V, L0, lit);\n'
assert hashlib.sha256(ROUND1_CALLS.encode()).hexdigest() == '15c6f6af784236a4048b6ae744e3e2d580b48324dc3a2bc2cdbc9dab5591793f'
OLD_DISPATCH = 'char *mat_clearcoat_shader_old(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id) {\n    char *base = mat_replace(tokens, n, refl, id);\n    if (!base || *id <= 0 || table[*id - 1].msl != car_paint_msl) { free(base); return NULL; }\n    char *coated = clearcoat_patch(base, 1);\n    free(base);\n    return coated;\n}'
assert hashlib.sha256(OLD_DISPATCH.encode()).hexdigest() == '9c879e24e8835bfda71ab4e73f6602e047d9cf72f751fcd5aa4c7c74ab519b0c'

RT = Path(__file__).resolve().parent


def run(args, expected=0, **kwargs):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kwargs)
    if p.returncode != expected:
        raise AssertionError(f'{args[0]}: exit {p.returncode}, expected {expected}\n{p.stdout[-2000:]}{p.stderr[-4000:]}')
    return p


def main():
    src = (RT / 'materials.m').read_text()
    body = src[src.index('static const char car_paint_msl'):src.index('\n// BOOT_DIAG_CARPAINT_AB=1')]
    assert hashlib.sha256(body.encode()).hexdigest() == '0f962aaaa7a8d77ef060109920da3b9ca53383da43ddbbe52aabda1af7255813', 'Off paint shader changed'
    # Independent integration boundaries: On gets a separate key, Off chooses
    # the original shader, and classification reaches the uploaded constant.
    render = mw_source(RT / 'render.m')
    for text in ('e->variant == MAT_VARIANT_CLEARCOAT ? mat_clearcoat_shader(',
                 'clearcoat_shader_hash(ps->tokens_hash) && clearcoat_enabled()',
                 'variant != MAT_VARIANT_CLEARCOAT',
                 'shader_preload_variant(&s, &opt, MAT_VARIANT_CLEARCOAT)'):
        assert text in render, text
    assert 'coat_material = clearcoat_is_paint((const char *)G + m + 0x14, 28);' in src
    assert 'o->k[10][3] = (float)coat_material;' in src
    assert 'clearcoat.c \\' in (RT / 'build.sh').read_text()
    assert 'clearcoat_test.py + clearcoat_test.m' in (RT / 'offline_tests.sh').read_text()
    assert 'NFS_CLEARCOAT\tuser\ton\truntime/clearcoat.c' in (RT / 'switches.tsv').read_text()
    tables = [RT / 'gen' / n for n in ('car_shadow_off.inc', 'pc_shader_lits.inc', 'x360_shader_lits.inc')]
    if any(not p.is_file() for p in tables) or '#define X360_SHADER_LITS 1' not in tables[-1].read_text():
        print('clearcoat_test: SKIP private shader literals missing')
        return 3
    t0_fixtures = [RT.parent / 'host/shaders/testdata/car' / name for name in
                   ('t0_car_p0_ps_1_1.bin', 't0_car_p0_vs_1_1.bin')]
    if any(not path.is_file() for path in t0_fixtures):
        print('clearcoat_test: SKIP private t0 shader fixtures missing')
        return 3
    pow_helper = src[src.index('#define CAR_PAINT_POW_MSL'):src.index('\n\nstatic const char car_paint_msl')]
    t0_helpers = '\n'.join(re.search(r'^static char \*'+name+r'\([^\n]+\n.*?^\}', src, re.M | re.S)[0] for name in ('dup_replace', 'car_vs_patch', 'car_t0_ps_patch'))
    t0_constants = re.search(r'^static void carbon_lamp_constants\([^\n]+\n.*?^\}', src, re.M | re.S)[0]
    wrapper = re.search(r'^char \*mat_clearcoat_shader\([^\n]+\n.*?^\}', src, re.M | re.S)[0]
    with tempfile.TemporaryDirectory(prefix='clearcoat-') as temp:
        td = Path(temp)
        emit = td / 'emit.c'
        emit.write_text('''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "clearcoat.h"
#include "settings.h"
#include "../host/shaders/sm_coverage.h"
#include "gen/car_shadow_off.inc"
#include "gen/pc_shader_lits.inc"
#include "gen/x360_shader_lits.inc"
static Setting *row;
void settings_add(Setting *s) { row=s; }
static const char *variant="";
int postfx_look_variant(const char *v) { return !strcmp(variant,v); }
typedef struct { float k[12][4]; } MatConsts;
static int coat_material,carbon_lamp,clear_headlamp,lamp_cover;
int headlamp_glass_on(void) { return 1; }
int lamp_ab_held(void) { return 0; }
int car_light_hdr(void) { return 1; }
''' + t0_constants + '\n' + t0_helpers + '\n' + pow_helper + '\n' + body + '''
static const struct { const char *msl; uint64_t hash; } table[]={{car_paint_msl,0},{"not paint",0},{NULL,0x89E9A52CCEE09BCFULL}};
static int fixture_id=1;
char *mat_replace(const uint32_t *tokens,uint32_t n,const SmResult *refl,int *id) {
    (void)tokens;(void)n;(void)refl;*id=fixture_id;
    return fixture_id ? strdup(car_paint_msl):NULL;
}
''' + 'static char *car_local_shader(char*s,int v,int t){(void)v;(void)t;return s;}\n' + wrapper + '\n' + OLD_DISPATCH + '''
int main(int argc,char **argv) {
    if(argc==5) {
        for(int vertex=0;vertex<2;vertex++) {
            FILE*f=fopen(argv[1+vertex],"rb");if(!f)return 3;fseek(f,0,SEEK_END);long bytes=ftell(f);rewind(f);
            uint32_t*tokens=malloc(bytes);fread(tokens,1,bytes,f);fclose(f);
            SmOptions options;sm_default_options(&options);SmResult refl={0};
            if(sm_translate(tokens,(uint32_t)bytes/4,&options,&refl))return 3;
            int id=0;fixture_id=3;
            char*old=mat_clearcoat_shader_old(tokens,(uint32_t)bytes/4,&refl,&id);
            if(old)return 4;
            char*out=mat_clearcoat_shader(tokens,(uint32_t)bytes/4,&refl,&id);
            if(!out || (!vertex && id!=3))return 5;
            char path[1024];snprintf(path,sizeof path,"%s/%s.metal",argv[3],vertex?"t0vs":"t0on");
            f=fopen(path,"wb");fputs(out,f);fclose(f);free(out);
            if(!vertex){
                char*base=car_t0_ps_patch(refl.msl);char*off=clearcoat_patch(base,0);if(strcmp(off,base))return 6;free(base);
                snprintf(path,sizeof path,"%s/t0off.metal",argv[3]);f=fopen(path,"wb");fputs(off,f);fclose(f);free(off);
                refl.samplers[0].type=SM_TEX_3D;
                if(mat_clearcoat_shader(tokens,(uint32_t)bytes/4,&refl,&id))return 7;
            }
            sm_result_free(&refl);free(tokens);
        }
        fprintf(stderr,"clearcoat_test: t0 native VS/PS dispatch old=0/2 new=2/2; malformed sampler rejected; Off bytes identical PASS\\n");
        return 0;
    }
    if(argc!=3 || !row || row->category!=SET_CARS || row->def!=1 || row->restart ||
       strcmp(row->label,"Clear coat") || strcmp(row->env,"NFS_CLEARCOAT") ||
       row->nchoices!=2 || strcmp(row->values[0],"off") || strcmp(row->values[1],"on")) return 1;
    float vsc[256][4]={{0}};MatConsts constants={0};
    for(int i=0;i<3;i++)vsc[7+i][0]=.1f*(i+1);
    coat_material=1;carbon_lamp_constants(vsc,&constants);
    if(constants.k[10][3]!=1)return 1;
    for(int i=0;i<3;i++)if(constants.k[1][i]!=vsc[7+i][0])return 1;
    coat_material=0;carbon_lamp_constants(vsc,&constants);if(constants.k[10][3]!=0)return 1;
    const char *coat_env=getenv("NFS_CLEARCOAT");int expected=!(coat_env && *coat_env) || !strcmp(coat_env,"on");
    if(clearcoat_enabled()!=expected)return 1;
    row->apply(1);if(!clearcoat_enabled())return 1;
    row->apply(0);if(clearcoat_enabled())return 1;
    variant="coaton";if(!clearcoat_enabled())return 1;
    variant="coatoff";if(clearcoat_enabled())return 1;
    const char *paint[]={"REGPAINTRED","METPAINTBLUE","PEARLBLACK"};
    const char *other[]={"WINDSHIELD","WINDOWMASK","TIRE","MISC","HOSES","CHROME","HEADLIGHTGLASS","PLAINNOTHING","REG",""};
    for(unsigned i=0;i<sizeof paint/sizeof *paint;i++)if(!clearcoat_is_paint(paint[i],strlen(paint[i])))return 1;
    for(unsigned i=0;i<sizeof other/sizeof *other;i++)if(clearcoat_is_paint(other[i],strlen(other[i])))return 1;
    if(clearcoat_is_paint("REGPAINTRED",3))return 1;
    uint64_t hashes[]={0x393123DA8B3EC17FULL,0x2CD941122737DDF5ULL,0xE3C84E78B9847542ULL,0x6F1A7154236357C9ULL,0xA8F9086D6BE6DB85ULL,0x89E9A52CCEE09BCFULL,0x1BEA38904139377AULL};
    for(unsigned i=0;i<sizeof hashes/sizeof *hashes;i++)if(!clearcoat_shader_hash(hashes[i]))return 1;
    if(clearcoat_shader_hash(0))return 1;
    SmResult refl={.shader_type=SM_PIXEL};
    if(clearcoat_patch("using namespace metal;\\n",1))return 1;
    int id;
    fixture_id=0;if(mat_clearcoat_shader(NULL,0,&refl,&id))return 1;
    fixture_id=2;if(mat_clearcoat_shader(NULL,0,&refl,&id))return 1;
    fixture_id=1;
    for(int on=0;on<2;on++) {
        char *out=on?mat_clearcoat_shader(NULL,0,&refl,&id):clearcoat_patch(car_paint_msl,0);
        if(!out || (!on && strcmp(out,car_paint_msl)) || (on && id!=1))return 1;
        FILE *f=fopen(argv[1+on],"wb");if(!f)return 1;
        fwrite(out,1,strlen(out),f);fclose(f);free(out);
    }
    fprintf(stderr,"clearcoat_test: settings live/held/default, paint gating, 7 hashes, Off source %zu bytes identical PASS\\n",strlen(car_paint_msl));
    return 0;
}
''')
        emitter = td / 'emit'
        run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-I', RT, '-c', emit, '-o', td / 'emit.o'])
        run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-I', RT, '-c', RT / 'clearcoat.c', '-o', td / 'coat.o'])
        run(['clang++', td / 'emit.o', td / 'coat.o', RT.parent / 'host/shaders/build/libsmtranslate.a', '-o', emitter])
        off, on = td / 'off.metal', td / 'on.metal'
        for value in (None, 'off', 'on', 'invalid'):
            env = dict(os.environ)
            env.pop('NFS_CLEARCOAT', None)
            if value is not None:
                env['NFS_CLEARCOAT'] = value
            p = run([emitter, off, on], env=env)
        print(p.stderr, end='')
        p = run([emitter, *t0_fixtures, td, 't0'])
        print(p.stderr, end='')
        original, coated = off.read_text(), on.read_text()
        assert coated.count('o.c0.rgb = coat_layer(') == 2
        assert 'X360' in original and 'car_spec_pow(rv,' in coated
        # Compile every shadow technique with software and hardware samplers.
        # Native fixture runs ordinary + normal-map + flake through sm_main.
        binary = td / 'test'
        run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', RT / 'clearcoat_test.m',
             '-framework', 'Foundation', '-framework', 'Metal', '-o', binary])
        args = [binary, off, on, td / 't0off.metal', td / 't0on.metal', td / 't0vs.metal']
        p = subprocess.run(list(map(str, args)), capture_output=True, text=True)
        evidence = [p.stdout + p.stderr]
        print(p.stdout + p.stderr, end='')
        if p.returncode:
            return p.returncode
        for control in ('lobe', 'attenuation', 'env'):
            p = run([*args, control], expected=1)
            evidence.append(p.stdout + p.stderr)
            print(p.stdout + p.stderr, end='')
            assert re.search(r'layer_failures=[1-9]\d*', p.stdout + p.stderr), control + ' was not detected numerically'
        # Reproduce the shipped normalization and env replacement as exact,
        # digest-pinned fragments, keeping today's fixture and Off source.
        helpers = re.search(r'static float3 coat_unit\(.*?static float3 coat_layer\(.*?\n}\n', coated, re.S)[0]
        baseline = coated.replace(helpers, ROUND1_MATH)
        current_calls = [line+'\n' for line in coated.splitlines() if 'o.c0.rgb = coat_layer(' in line]
        assert len(current_calls) == 2
        for current, old in zip(current_calls, ROUND1_CALLS.splitlines(True)[:2]):
            baseline = baseline.replace(current, old)
        baseline_path = td / 'round1.metal'
        baseline_path.write_text(baseline)
        p = run([binary, off, baseline_path, *args[3:]], expected=1)
        evidence.append(p.stdout+p.stderr)
        print('round-1 baseline: '+p.stderr, end='')
        for field in ('highlight_failures', 'grazing_failures', 'transmission_failures', 'diffuse_failures'):
            assert re.search(field+r'=[1-9]\d*', p.stderr), field+' did not reject the shipped math'
        # Keep the directional-cube regression independently of base layering.
        baseline_path = td / 'reflection.metal'
        baseline_path.write_text(coated.replace('tex1.sample(smp1, float3(-Vt.xy, Vt.z))', 'tex1.sample(smp1, Rt)'))
        p = run([binary, off, baseline_path, *args[3:]], expected=1)
        evidence.append(p.stdout+p.stderr)
        print('broken reflection: '+p.stderr, end='')
        assert re.search(r'smooth_failures=[1-9]\d*', p.stderr), 'rough coat reflection escaped the check'
        print('clearcoat_test: normal=0 lobe-removed=1 attenuation-removed=1 env-doubled=1 PASS')
        logs = RT.parent / '.metal-cache'
        logs.mkdir(exist_ok=True)
        (logs / 'clearcoat-fixture.log').write_text(''.join(evidence))
    return 0


if __name__ == '__main__':
    sys.exit(main())
