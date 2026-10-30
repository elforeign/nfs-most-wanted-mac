#!/usr/bin/env python3
"""Pedals: production preset/hook/roles/prompts + translated binding evaluator.
ASan/UBSan; retail action table, fake independent physical trigger state.
PASS: 3,094 checks / 0 failures; thresholded input: 300 / 3,094 failures;
old layout: 306 / 3,094; missing restore: 255 / 3,094 (all rejected).
Physics log checker: .25 -> .25 passes; thresholded .25 -> 1 fails (+.75).
Measured sim physics sweep (post-ComputeState): both pedals 0/.249989/.499992/
.749996/1; 11/11 pairs pass; thresholded measured-copy .25 -> 1 is rejected.
"""
from pathlib import Path
import importlib.util
import os
import re
import struct
import subprocess
import tempfile

R = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, R/(name+'.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


MAIN = r'''
static void physical(float lt,float rt,int buttons,int threshold) {
    memset(fake_pad,0,sizeof fake_pad);
    for(int i=0;i<3;i++) { fake_put(0,4*i,32768); fake_put(0,0x20c+4*i,32768); }
    fake_put(0,8,(uint32_t)lroundf(32767.5f*(1+(threshold && lt>0 ? 1 : lt))));
    fake_put(0,0x214,(uint32_t)lroundf(32767.5f*(1+(threshold && rt>0 ? 1 : rt))));
    fake_put(0,0x220,0xffffffff);
    for(int i=0;i<16;i++) fake_put(0,0xc+4*i,(buttons>>i)&1 ? 128 : 0);
}
int main(int argc,char **argv) {
    G=calloc(1,0x1000000); FILE *file=fopen(argv[1],"rb"); if(!file) return 2;
    if(fread(G,1,0x1000000,file)!=0x1000000) return 2; fclose(file);
    MF32(0x890968)=0; MF32(0x89096c)=1; MF32(0x8a7a58)=32767.5f;
    MF32(0x8a7a5c)=.0078125f; MF32(0x8957bc)=3.0518044e-05f;
    Cpu c=cpu(d[0]); M32(c.esp+4)=0; f_00634B50(&c); abi(&c,1);
    M32(0x982bec)=1; strcpy(joy_name(joy()),"Controller (DualSense)");
    CHECK(s_pedals.def==0 && !pedals_selected());
    for(int a=0;a<19;a++) for(int b=0;b<4;b++) bind(a,b,0,0,0);
    bind(0,2,T_BUTTON,0,P_CROSS); bind(1,2,T_BUTTON,0,P_SQUARE);
    bind(7,2,T_AXIS_HI,0,2); bind(8,2,T_ROT_HI,0,2);
    bind(4,2,T_BUTTON,0,P_R1); bind(6,2,T_BUTTON,0,P_L1);
    uint8_t saved[76*52]; memcpy(saved,G+GA,sizeof saved);
    for(int on=0;on<2;on++) {
        s_pedals.value=on; CHECK(padbind_pedals(0)==on);
        CHECK(padbind_trigger_role(0)==(on?PADBIND_ROLE_BRAKE:PADBIND_ROLE_SHIFT));
        CHECK(padbind_trigger_role(1)==(on?PADBIND_ROLE_GAS:PADBIND_ROLE_SHIFT));
        CHECK(padbind_ps_label(6,0)==(on?0xDC1CD97Du:0x619B23B0u));
        CHECK(padbind_ps_label(7,0)==(on?0x56AFE190u:0x6AB80AFAu));
        CHECK(padbind_ps_label(12,0)==(on?0x6AB80AFAu:0x56AFE190u));
        CHECK(padbind_ps_label(13,0)==(on?0x619B23B0u:0xDC1CD97Du));
        CHECK(!memcmp(saved,G+GA,sizeof saved));
    }
    for(int type=1;type<=10;type++) {
        s_type.value=type-1; s_pedals.value=1;
        for(int li=0;li<5;li++) for(int ri=0;ri<5;ri++) {
            float lt=li*.25f,rt=ri*.25f;
            physical(lt,rt,0,argc>2); c=cpu(d[0]); f_006349B0(&c); abi(&c,0);
            CHECK(fabsf(value(0,0)-rt)<.0001f); CHECK(fabsf(value(0,1)-lt)<.0001f);
            CHECK(fabsf(value(0,7))<.0001f && fabsf(value(0,8))<.0001f);
            CHECK(!memcmp(saved,G+GA,sizeof saved)); CHECK(swaps==0);
        }
        physical(0,0,1<<P_CROSS,0); c=cpu(d[0]); f_006349B0(&c);
        CHECK(value(0,8)==1 && fabsf(value(0,0))<.0001f && fabsf(value(0,1))<.0001f);
        physical(0,0,1<<P_SQUARE,0); c=cpu(d[0]); f_006349B0(&c);
        CHECK(value(0,7)==1 && fabsf(value(0,0))<.0001f && fabsf(value(0,1))<.0001f);
    }
    // Unchanged nitrous/handbrake, keyboard input, capture and exact Shift restoration.
    s_type.value=0;
    physical(0,0,(1<<P_L1)|(1<<P_R1),0); c=cpu(d[0]); f_006349B0(&c);
    CHECK(value(0,4)==1 && value(0,6)==1);
    bind(0,0,T_KEY,0,42); M8(0x100000+0x2a)=128;
    physical(0,0,0,0); c=cpu(d[0]); f_006349B0(&c); CHECK(value(0,0)==1);
    CHECK(M32(rec(0,0))==T_KEY && M32(rec(0,0)+8)==42);
    bind(0,0,0,0,0);
    s_pedals.value=0; physical(0,0,1<<P_CROSS,0); c=cpu(d[0]); f_006349B0(&c);
    CHECK(value(0,0)==1 && fabsf(value(0,8))<.0001f); CHECK(!memcmp(saved,G+GA,sizeof saved));
    s_pedals.value=1; M8(d[0]+0x183c)=1;
    c=cpu(d[0]); f_006349B0(&c); CHECK(!memcmp(saved,G+GA,sizeof saved)); M8(d[0]+0x183c)=0;
    strcpy(joy_name(joy()),"Controller (Xbox)"); CHECK(!padbind_pedals(0));
    physical(0,0,1<<P_CROSS,0); c=cpu(d[0]); f_006349B0(&c); CHECK(value(0,0)==1);
    printf("pedals: %d checks, %d failures\n",checks,failures);
    free(G); return failures!=0;
}
'''


def checker_proof():
    checker = module('pedals_check')
    rows = []
    for index, (lt, rt) in enumerate(checker.CASES):
        rows.extend(dict(step=2*index+1, lt=lt, rt=rt, gas=rt, brake=lt,
                         speed=20, input_gas=rt, input_brake=lt) for _ in range(3))
    assert len(checker.check(rows)) == 11
    for broken in ('threshold', 'autobrake', 'reverse', 'missing', 'transient'):
        bad = [dict(row) for row in rows]
        if broken == 'threshold':
            for row in bad:
                row['gas'] = row['input_gas'] = int(row['rt'] > 0)
        elif broken in ('autobrake', 'reverse'):
            for row in bad:
                row['speed'] = 0
        elif broken == 'missing':
            bad = bad[3:]
        else:
            bad[3]['gas'] = 1
        try:
            checker.check(bad)
        except ValueError as error:
            print(f'checker rejected {broken}: {error}')
        else:
            raise AssertionError(f'checker accepted {broken}')


def main():
    exe = Path(os.environ.get('NFS_GAME', str(R.parent.parent/'Need For Speed Most Wanted Black Edition')))/'speed.exe'
    if not exe.exists():
        print('SKIP: needs retail speed.exe')
        return 3
    data = exe.read_bytes(); pe = struct.unpack_from('<I', data, 0x3c)[0]
    ns = struct.unpack_from('<H', data, pe+6)[0]; opt = struct.unpack_from('<H', data, pe+20)[0]
    base = struct.unpack_from('<I', data, pe+52)[0]; memory = bytearray(0x1000000)
    for i in range(ns):
        _, vs, va, rs, ro = struct.unpack_from('<8sIIII', data, pe+24+opt+40*i)
        memory[base+va:base+va+rs] = data[ro:ro+rs]
    h = module('padguest_test')
    pre = h.PRE.replace('int padbind_pedals(int port) { (void)port; return 0; }', '')
    pre = re.sub(r'\bput\(', 'fake_put(', pre)
    pre += '\nint pad_identity_n(int port,char *name,size_t n) { (void)port; snprintf(name,n,"Controller (DualSense)"); return PAD_KIND_DUALSENSE; }\n'
    pb = (R/'padbind.c').read_text()
    defs = pb[pb.index('#define GA '):pb.index('static uint32_t name_hash')]
    helpers = pb[pb.index('static int name_ps('):pb.index('static const char *action_name(')]
    labels = pb[pb.index('uint32_t padbind_ps_label('):]
    bodies = '\n'.join(h.translated(file, name) for file, names in (
        ('speed_31.c', ('00634B50', '006349B0')),
        ('speed_30.c', ('00628940', '006284A0')),
    ) for name in names)
    hook = (R/'controltype.c').read_text()
    sources = {'normal': hook, 'thresholded': hook,
               'old-layout': hook.replace('int override = pedal_layout && pedals_binding(a, pedal);', 'int override = 0;'),
               'missing-restore': hook.replace('M32(r + 4 * (uint32_t)k) = saved[i][k];', '(void)saved[i][k];')}
    with tempfile.TemporaryDirectory(prefix='pedals-') as tmp:
        tmp = Path(tmp); (tmp/'memory').write_bytes(memory)
        for name, source in sources.items():
            assert name in ('normal', 'thresholded') or source != hook
            path = tmp/(name+'.c'); out = tmp/name
            path.write_text('\n'.join((pre, '#define PEDALS_NO_TRACE 1\n#include "pedals.c"',
                                      'int pad_count(void) { return 1; }', defs, helpers, labels,
                                      bodies, source, MAIN)))
            h.run(['clang', '-std=c11', '-g', '-w', '-DRECOMP_ABICHECK', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-I', str(R), '-I', str(R.parent/'recomp'),
                   '-I', str(R.parent/'recomp/out'), str(path), '-lm', '-o', str(out)])
            args = [out, tmp/'memory'] + (['threshold'] if name == 'thresholded' else [])
            p = subprocess.run(args, capture_output=True, text=True)
            assert p.returncode == (0 if name == 'normal' else 1), p.stdout[-1500:]+p.stderr[-1500:]
            print(name+': '+p.stdout.strip().splitlines()[-1])
    checker_proof()
    print('pedals_test: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
