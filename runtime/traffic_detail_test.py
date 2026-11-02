"""Production traffic glass eligibility and translated LOD selection/clamps.

Original glass/LOD paths: 603 failures / 16657 checks; fixed: 0 / 16657.
Original-path control and fixed results are printed with assertion counts.
Held pairs: 18 checks pass; wrong-restore control gives 2 failures.
The full offline suite discovers this test; no game launch or emulation.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'recomp'))
from traffic_detail import traffic_detail_lod


def function(src, name):
    start = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^\n]*\) \{', src, re.M).start()
    return src[start:src.index('\n}', start) + 2]


rt = ROOT / 'runtime'
assert 'if (diag_on) traffic_detail_ab_shot(shot_path, sizeof shot_path);' in (rt/'render_present.m').read_text()
assert '(diag_on && traffic_detail_ab_hold())' in (rt/'pacing.c').read_text()
source = (ROOT / 'recomp/out/speed_47.c')
if not source.exists():
    print('SKIP: build recompiler first')
    raise SystemExit(3)
code = source.read_text()
start = code.index('    /* 0074E4DC:')
end = code.index('    /* 0074E570:', start)
lod = code[start:end]
raw = lod.replace('traffic_racer_lod:\n', '').replace('    if (traffic_detail_on()) goto traffic_racer_lod;\n', '')
fixed = traffic_detail_lod(0x0074E160, raw)
assert lod == fixed, "rebuild recompiler: traffic LOD hook is stale"
assert traffic_detail_lod(0, raw) == raw
try:
    traffic_detail_lod(0x0074E160, raw.replace('L_0074E51D:;', 'L_0074E51E:;'))
except AssertionError:
    pass
else:
    raise AssertionError('instruction drift accepted')

materials = (rt / 'materials.m').read_text()
physical = (rt / 'realworld_materials.c').read_text()
glass = function(materials, 'glass_find')
role = function(physical, 'realworld_materials_role')
hash_name = function(physical, 'hash_name')
header = '''#include "runtime.h"
#include "traffic_detail.c"
#include <stdio.h>
uint8_t *G;
typedef struct GlassX360 { uint32_t hash; float min_a,max_a,env_min,env_max; } GlassX360;
// Synthetic distinct values prove aliasing reuses the existing row verbatim.
static const GlassX360 glass_x360[]={{0x471A1DCAu,.1f,.2f,.3f,.4f}};
#define GLASS_X360_COUNT 1
static unsigned checks, failures;
#define CHECK(x) do { ++checks; if(!(x)) ++failures; } while(0)
'''
main = r'''
static int ab_test(void) {
    unsigned errors=0;
    char path[512];
    for(int on=0;on<2;++on) {
        traffic_detail_set(on);
        snprintf(path,sizeof path,"/tmp/simshot_030.00.png");
        errors+=traffic_detail_ab_shot(path,sizeof path)!=0;
        errors+=traffic_detail_ab_hold()!=1;
        errors+=traffic_detail_on()!=!on;
        path[0]=0;
        errors+=traffic_detail_ab_shot(path,sizeof path)!=1;
        errors+=strcmp(path,on ? "/tmp/simshot_030.00_traffic_off.png" : "/tmp/simshot_030.00_traffic_on.png")!=0;
        errors+=traffic_detail_ab_hold()!=1;
        errors+=traffic_detail_on()!=on;
        errors+=traffic_detail_ab_hold()!=0;
    }
    ab_enabled=0;
    snprintf(path,sizeof path,"/tmp/simshot_040.00.png");
    errors+=traffic_detail_ab_shot(path,sizeof path)!=0;
    errors+=traffic_detail_ab_hold()!=0;
    printf("held pair: 18 checks, %u failures\n",errors);
    return errors!=0;
}
static void select_lod(Cpu *c) {
    uint32_t cf=0,of=0,zf=0,sf=0,pf=0,af=0;
LOD
}
int main(int argc,char **argv) {
    if(argc>2) return ab_test();
    if(argc>1) return traffic_detail_on()!=atoi(argv[1]);
    G=calloc(1,0xa00000);
    CHECK(traffic_detail_on()==0);
    for(int on=0;on<2;++on) {
        traffic_detail_set(on);
        CHECK(realworld_materials_role(0x1FF8C329u,1,1,0x80000u)==on);
        CHECK(realworld_materials_role(0x1FF8C329u,1,3,0x80000u)==on);
        CHECK(realworld_materials_role(0x1FF8C329u,0,3,0x80000u)==0);
        CHECK(realworld_materials_role(0x1FF8C329u,1,2,0x80000u)==0);
        CHECK(realworld_materials_role(0x471A1DCAu,1,1,0x80000u)==1);
        CHECK(glass_find(0x1FF8C329u,0x80000u)==(on ? &glass_x360[0] : NULL));
        CHECK(glass_find(0x471A1DCAu,0x80000u)==&glass_x360[0]);
        CHECK(glass_find(0xdeadbeefu,0x80000u)==NULL);
        float racer[]={120,25,20,10,0},traffic[]={80,40,20,10,0};
        memcpy(G+0x8b2ebc,racer,sizeof racer); memcpy(G+0x8b2ed0,traffic,sizeof traffic);
        MF32(0x8970c0)=120; MF32(0x8b2f7c)=80;
        for(int traf=0;traf<2;++traf) for(int base=0;base<=2;base+=2)
        for(int maximum=base;maximum<=4;++maximum) for(int px=1;px<=130;++px) {
            Cpu c={0}; c.esp=0x10000;c.esi=0x20000;c.ebp=0x30000;
            c.eax=traf;c.edi=0;c.cw=0x37f; M32(c.ebp+0x20)=0;
            M32(c.esp+0x10)=px;M32(c.esi+0x1628)=base;M32(c.esi+0x162c)=maximum;
            const float *table=traf ? (on ? racer : traffic) : (base==2 ? traffic : racer);
            int i=0;while(px<table[i])++i;
            int expected=base+i; if(expected>maximum)expected=maximum;
            select_lod(&c);
            CHECK(M32(c.esp+0x14)==expected);
            CHECK(M32(c.esp+0x58)==expected);
            CHECK(M32(c.ebp+0x20)==(traf ? 0x80000u : 0));
            CHECK(c.top==0);
        }
    }
    printf("traffic detail: %u checks, %u failures\n",checks,failures);
    free(G);return failures!=0;
}
'''
with tempfile.TemporaryDirectory(prefix='traffic-detail-') as td:
    td = Path(td)
    for name, body, material_glass, material_role in [('original', raw, glass.replace('hash = traffic_detail_glass(hash, flags);', ''), role.replace('h = traffic_detail_glass(h, flags);', '')),
                                                   ('fixed', fixed, glass, role)]:
        unit = td / 'check.c'
        unit.write_text(header + hash_name + material_glass + material_role + main.replace('LOD', body))
        exe = td / name
        p = subprocess.run(['clang','-O1','-I',str(rt),'-I',str(ROOT/'recomp'),str(unit),'-o',str(exe)],capture_output=True,text=True)
        assert p.returncode == 0, p.stderr[-3000:]
        p = subprocess.run([str(exe)], capture_output=True, text=True, env={**os.environ,'NFS_TRAFFIC_DETAIL':'off'})
        print(name, p.stdout.strip(), flush=True)
        assert p.returncode == (1 if name == 'original' else 0), p.stderr[-2000:]
        if name == 'fixed':
            for value, expected in [(None,0),('off',0),('on',1),('',0),('ON',0),('garbage',0)]:
                env = dict(os.environ)
                env.pop('NFS_TRAFFIC_DETAIL', None)
                if value is not None:
                    env['NFS_TRAFFIC_DETAIL'] = value
                assert subprocess.run([str(exe),str(expected)],env=env).returncode == 0, value
            print('PASS: 6 environment/default cases', flush=True)
            env = {**os.environ, 'BOOT_DIAG_TRAFFIC_AB':'1'}
            assert subprocess.run([str(exe),'ab','test'],env=env).returncode == 0
            bad_module = (rt/'traffic_detail.c').read_text().replace('traffic_detail_set(ab_saved);', 'traffic_detail_set(!ab_saved);')
            assert bad_module != (rt/'traffic_detail.c').read_text()
            (td/'traffic_bad.c').write_text(bad_module)
            unit.write_text(unit.read_text().replace('#include "traffic_detail.c"', '#include "traffic_bad.c"'))
            p = subprocess.run(['clang','-O1','-I',str(rt),'-I',str(ROOT/'recomp'),str(unit),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode == 0, p.stderr[-2000:]
            assert subprocess.run([str(exe),'ab','test'],env=env).returncode == 1
            print('PASS: held-pair restore mutant rejected', flush=True)
print('PASS: original glass/LOD paths rejected; enabled and disabled paths, boundaries and loaded bounds checked')
