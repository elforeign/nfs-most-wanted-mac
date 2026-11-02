#!/usr/bin/env python3
"""Native trigger bindings: real preference loader/registry, hook, roles and labels.
ASan/UBSan; retail memory, translated capture/insertion/evaluation; fake pad only.
PASS: 334 checks / 0 failures; thresholded pressure: 30 / 334 failures;
wrong R2 capture axis: 23 / 334 failures (both rejected).
Pinned e09a0440: 10 / 405 failures (F10 row: 2; right-stick evaluation: 8).
Physics checker rejects thresholded, stationary, missing and transient samples.
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
static void physical(float lt,float rt,float stick,int threshold) {
    memset(fake_pad,0,sizeof fake_pad);
    for(int i=0;i<3;i++) { fake_put(0,4*i,32768); fake_put(0,0x20c+4*i,32768); }
    fake_put(0,8,(uint32_t)lroundf(32767.5f*(1+(threshold && lt>0 ? 1 : lt))));
    fake_put(0,0x214,(uint32_t)lroundf(32767.5f*(1+(threshold && rt>0 ? 1 : rt))));
    fake_put(0,0x210,(uint32_t)lroundf(32767.5f*(1+stick)));
    fake_put(0,0x220,0xffffffff);
}
static void capture(int action,int kind,float lt,float rt) {
    physical(lt,rt,0,0);
    memcpy(G+0x121000,fake_pad[0],0x224);
    uint32_t object=0x350000;
    M32(object+4)=joy(); M32(joy()+8)=3;
    M32(object+0xc)=kind; M32(object+0x10)=0;
    M32(d[0]+0x1844)=2; M32(d[0]+0x1848)=action;
    Cpu c=cpu(d[0]); M32(c.esp+4)=object;
    f_00634490(&c); abi(&c,1); CHECK((c.eax&255)==1);
}
int main(int argc,char **argv) {
    scratch=argv[2]; settings_init();
    CHECK(!strcmp(app_pref("controls.triggers"),"pedals"));
    CHECK(settings_find("controls.triggers")==NULL);
    Setting *rows[128]; int count=settings_rows(SET_CONTROLS,rows,128);
    for(int i=0;i<count;i++) CHECK(strcmp(rows[i]->label,"Triggers"));
    int row_failures=failures;
    G=calloc(1,0x1000000); FILE *file=fopen(argv[1],"rb"); if(!file) return 2;
    if(fread(G,1,0x1000000,file)!=0x1000000) return 2; fclose(file);
    MF32(0x890968)=0; MF32(0x89096c)=1; MF32(0x8a7a58)=32767.5f;
    MF32(0x8a7a5c)=.0078125f; MF32(0x8957bc)=3.0518044e-05f;
    Cpu c=cpu(d[0]); M32(c.esp+4)=0; f_00634B50(&c); abi(&c,1);
    M32(0x982bec)=1; strcpy(joy_name(joy()),"Controller (DualSense)");
    for(int a=0;a<19;a++) for(int b=0;b<4;b++) bind(a,b,0,0,0);
    // A saved Pedals preference must not mask either joystick slot, including the right stick.
    bind(0,2,T_BUTTON,0,P_CROSS); bind(1,2,T_BUTTON,0,P_SQUARE);
    bind(0,3,T_ROT_LO,0,1); bind(1,3,T_ROT_HI,0,1);
    bind(7,2,T_AXIS_HI,0,2); bind(8,2,T_ROT_HI,0,2);
    uint8_t saved[76*52]; memcpy(saved,G+GA,sizeof saved);
    for(int i=-4;i<=4;i++) {
        float stick=i*.25f;
        physical(0,0,stick,0); c=cpu(d[0]); f_006349B0(&c); abi(&c,0);
        CHECK(fabsf(value(0,0)-fmaxf(0,-stick))<.0001f);
        CHECK(fabsf(value(0,1)-fmaxf(0,stick))<.0001f);
        CHECK(!memcmp(saved,G+GA,sizeof saved)); CHECK(swaps==0);
    }
    int stick_failures=failures-row_failures;
    printf("legacy preference: row failures=%d; right-stick failures=%d\n",row_failures,stick_failures);
    // The Controls screen uses these translated capture + insertion functions.
    for(int i=0;i<3;i++) { M32(BASELINE+4*i)=32768; M32(0x91f3f4+4*i)=32768; }
    capture(A_GAS,4,0,1);
    CHECK(M32(rec(A_GAS,2))==7 && M32(rec(A_GAS,2)+4)==0 && M32(rec(A_GAS,2)+8)==2);
    CHECK(M32(rec(A_GAS,3))==T_ROT_LO && M32(rec(A_GAS,3)+8)==1);
    capture(A_BRAKE,3,1,0);
    CHECK(M32(rec(A_BRAKE,2))==5 && M32(rec(A_BRAKE,2)+4)==0 && M32(rec(A_BRAKE,2)+8)==2);
    CHECK(M32(rec(A_BRAKE,3))==T_ROT_HI && M32(rec(A_BRAKE,3)+8)==1);
    CHECK(padbind_trigger_role(0)==PADBIND_ROLE_BRAKE && padbind_trigger_role(1)==PADBIND_ROLE_GAS);
    CHECK(padbind_ps_label(12,0)==0x6AB80AFAu && padbind_ps_label(13,0)==0x619B23B0u);
    memcpy(saved,G+GA,sizeof saved);
    for(int li=0;li<5;li++) for(int ri=0;ri<5;ri++) {
        float lt=li*.25f,rt=ri*.25f;
        physical(lt,rt,0,argc>3); c=cpu(d[0]); f_006349B0(&c); abi(&c,0);
        CHECK(fabsf(value(0,0)-rt)<.0001f); CHECK(fabsf(value(0,1)-lt)<.0001f);
        CHECK(!memcmp(saved,G+GA,sizeof saved)); CHECK(swaps==0);
    }
    bind(0,0,T_KEY,0,42); M8(0x100000+0x2a)=128;
    physical(0,0,0,0); c=cpu(d[0]); f_006349B0(&c); CHECK(value(0,0)==1);
    CHECK(M32(rec(0,0))==T_KEY && M32(rec(0,0)+8)==42);
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
    pre = h.PRE.replace('void settings_add(Setting *s) { (void)s; }', '')
    pre = re.sub(r'\bput\(', 'fake_put(', pre)
    pre += '\nint pad_identity_n(int port,char *name,size_t n) { (void)port; snprintf(name,n,"Controller (DualSense)"); return PAD_KIND_DUALSENSE; }\nstatic const char *scratch;\nconst char *userdata_dir(void) { return scratch; }\nvoid report_note(const char *fmt, ...) { (void)fmt; }\n'
    app = (R/'app.m').read_text().split('// BEGIN offline prefs', 1)[1].split('\n', 1)[1].split('// END offline prefs', 1)[0]
    pre += '\n#include <errno.h>\n#include <unistd.h>\n#include <fcntl.h>\n#include "durable.h"\n' + app
    pre += '\n#include "settings.c"\n'
    bodies = '\n'.join(h.translated(file, name) for file, names in (
        ('speed_31.c', ('00634B50', '006349B0', '00634490')),
        ('speed_30.c', ('00628940', '006284A0', '006285F0')),
    ) for name in names)
    baseline = 'e09a0440e03d49211b98bd12e3268ca1d722e910'
    def old(file):
        return h.run(['git', 'show', baseline+':runtime/'+file], cwd=R.parent)
    hook = (R/'controltype.c').read_text()
    pb = (R/'padbind.c').read_text()
    def parts(pad):
        return (pad[pad.index('#define GA '):pad.index('static uint32_t name_hash')],
                pad[pad.index('static int name_ps('):pad.index('static const char *action_name(')],
                'int padbind_effective_binding(int set,int a,uint32_t out[6]) { (void)set; memcpy(out,G+rec(a,2),24); return 1; }\n' + pad[pad.index('uint32_t padbind_ps_label('):])
    sources = {'normal': (hook, pb, (R/'pedals.c').read_text()),
               'thresholded': (hook, pb, (R/'pedals.c').read_text()),
               'wrong-capture': (hook, pb, (R/'pedals.c').read_text()),
               'original': (old('controltype.c'), old('padbind.c'), old('pedals.c'))}
    with tempfile.TemporaryDirectory(prefix='pedals-') as tmp:
        tmp = Path(tmp); (tmp/'memory').write_bytes(memory)
        (tmp/'app.txt').write_text('controls.triggers=pedals\n')
        for name, (source, pad, pedals) in sources.items():
            path = tmp/(name+'.c'); out = tmp/name
            # Old declarations let the pinned implementation compile with today's host header.
            decl = 'int pedals_selected(void); int pedals_binding(int a,uint32_t out[6]); int padbind_pedals(int port);'
            capture = h.translated('speed_31.c', '00634490')
            wrong = capture.replace('c->ecx = (uint32_t)((uint32_t)0x7u);',
                                    'c->ecx = (uint32_t)((uint32_t)0x5u);')
            assert wrong != capture
            translated = bodies.replace(capture, wrong) if name == 'wrong-capture' else bodies
            path.write_text('\n'.join((pre, decl, '#define PEDALS_NO_TRACE 1', pedals,
                                      'int pad_count(void) { return 1; }', *parts(pad),
                                      translated, source, MAIN)))
            h.run(['clang', '-std=c11', '-g', '-w', '-DRECOMP_ABICHECK', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-I', str(R), '-I', str(R.parent/'recomp'),
                   '-I', str(R.parent/'recomp/out'), str(path), '-lm', '-o', str(out)])
            args = [out, tmp/'memory', tmp] + (['threshold'] if name == 'thresholded' else [])
            env = dict(os.environ)
            env.pop('NFS_CONTROL_TYPE', None); env.pop('NFS_TRIGGER_LAYOUT', None)
            env['NFS_ROOTS'] = str(tmp/'no-roots')
            p = subprocess.run(args, capture_output=True, text=True, env=env)
            assert p.returncode == (0 if name == 'normal' else 1), p.stdout[-2000:]+p.stderr[-2000:]
            assert not p.stderr, p.stderr[-2000:]
            if name == 'original':
                row, stick = map(int, re.search(r'row failures=(\d+); right-stick failures=(\d+)', p.stdout).groups())
                assert row > 0 and stick > 0
            print(name+': '+next(line for line in p.stdout.splitlines() if line.startswith('legacy preference:')))
            print(name+': '+p.stdout.strip().splitlines()[-1])
    checker_proof()
    print('pedals_test: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
