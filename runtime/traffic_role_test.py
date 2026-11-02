"""Traffic driver role regression (glass and translated LOD), without a game.

Reviewed pre-fix sources: 2,600 failures / 21,840 checks; fixed: 0 / 21,840.
TRAFFIC_ROLE_SOURCE_ROOT selects a frozen source snapshot for before/after proof.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('TRAFFIC_ROLE_SOURCE_ROOT', ROOT))


def function(text, name):
    start = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^\n]*\) \{', text, re.M).start()
    return text[start:text.index('\n}', start) + 2]


module = (SOURCE/'runtime/traffic_detail.c').read_text()
materials = (SOURCE/'runtime/materials.m').read_text()
physical = (SOURCE/'runtime/realworld_materials.c').read_text()
glass = function(materials, 'glass_find')
role = function(physical, 'realworld_materials_role')
# Adapt only calling conventions, so the same behavioral assertions run on the
# reviewed implementations and the new explicit render-flags API.
glass_call = 'glass_find(0x1FF8C329u' + (', flags)' if 'uint32_t flags' in glass else ')')
role_call = 'realworld_materials_role(0x1FF8C329u, 1, 3' + (', flags)' if 'uint32_t flags' in role else ')')
code = (ROOT/'recomp/out/speed_47.c').read_text()
start = code.index('    /* 0074E23F:')
end = code.index('    /* 0074E261:', start)
decode = code[start:end]
start = code.index('    /* 0074E4D4:')
end = code.index('    /* 0074E570:', start)
lod = code[start:end]
harness = r'''#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
uint8_t *G;
MODULE
typedef struct GlassX360 { uint32_t hash; float min_a,max_a,env_min,env_max; } GlassX360;
static const GlassX360 glass_x360[]={{0x471A1DCAu,.1f,.2f,.3f,.4f}};
#define GLASS_X360_COUNT 1
GLASS
HASH
ROLE
static void decode_role(Cpu *c) {
    uint32_t cf=0,of=0,zf=0,sf=0,pf=0,af=0;
    c->edx=0;
DECODE
}
static void select_lod(Cpu *c) {
    uint32_t cf=0,of=0,zf=0,sf=0,pf=0,af=0;
LOD
}
int main(void) {
    G=calloc(1,0xa00000);
    unsigned checks=0,failures=0;
#define CHECK(x) do { ++checks; if(!(x)) ++failures; } while(0)
    float racer[]={120,25,20,10,0},traffic[]={80,40,20,10,0};
    memcpy(G+0x8b2ebc,racer,sizeof racer); memcpy(G+0x8b2ed0,traffic,sizeof traffic);
    MF32(0x8970c0)=120; MF32(0x8b2f7c)=80;
    // Driver classes 0/1/3/4 and no PVehicle must keep player/front-end glass
    // and LOD, even on the same GARB/TAXI meshes as ambient traffic (class 2).
    for(int on=0;on<2;++on) for(int driver=-1;driver<=4;++driver)
    for(int base=0;base<=2;base+=2) for(int px=1;px<=130;++px) {
        traffic_detail_set(on);
        Cpu c={0}; c.esp=0x10000;c.esi=0x20000;c.ebp=0x30000;c.cw=0x37f;
        M32(c.esi+0x88)=driver<0 ? 0 : 0x40000;
        M32(0x40000+0x94)=driver;
        decode_role(&c);
        int traf=driver==2;
        CHECK(M32(c.esp+0x28)==traf);
        c.edi=0;M32(c.ebp+0x20)=0;
        M32(c.esp+0x10)=px;M32(c.esi+0x1628)=base;M32(c.esi+0x162c)=4;
        select_lod(&c);
        uint32_t flags=M32(c.ebp+0x20);
        CHECK(flags==(traf ? 0x80000u : 0));
        CHECK((GLASS_CALL!=NULL)==(on && traf));
        CHECK(ROLE_CALL==(on && traf));
        const float *table=traf ? (on ? racer : traffic) : (base==2 ? traffic : racer);
        int i=0;while(px<table[i])++i;
        int expected=base+i;if(expected>4)expected=4;
        CHECK(M32(c.esp+0x14)==expected);
        CHECK(M32(c.esp+0x58)==expected);
        CHECK(c.top==0);
    }
    printf("traffic roles: %u checks, %u failures\n",checks,failures);
    free(G); return failures!=0;
}
'''
for key,value in {'MODULE':module,'GLASS':glass,'HASH':function(physical,'hash_name'),
                  'ROLE':role,'DECODE':decode,'LOD':lod,'GLASS_CALL':glass_call,'ROLE_CALL':role_call}.items():
    harness = re.sub(r'(?m)^'+key+r'$' if key not in ('GLASS_CALL','ROLE_CALL') else r'\b'+key+r'\b', lambda m:value, harness)
with tempfile.TemporaryDirectory(prefix='traffic-role-') as td:
    unit=Path(td)/'check.c';exe=Path(td)/'check'
    unit.write_text(harness)
    p=subprocess.run(['clang','-O1','-I',str(SOURCE/'runtime'),'-I',str(ROOT/'runtime'),'-I',str(ROOT/'recomp'),str(unit),'-o',str(exe)],capture_output=True,text=True)
    assert p.returncode==0,p.stderr[-3000:]
    p=subprocess.run([str(exe)],capture_output=True,text=True)
    print(p.stdout.strip())
    assert p.returncode==0,p.stderr[-3000:]

# Both draw consumers must receive flags from the current material setter call.
assert 'glass_find(M32(m + 0xC), arg(c, 1))' in materials
assert 'realworld_materials_capture(m ? M32(m + 0xC) : 0, m && fx && M32(fx + 4) == 4, arg(c, 1))' in materials
assert 'realworld_materials_role(h, car, realworld_materials_mode(), flags)' in physical
