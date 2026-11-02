"""Installed-pack kit lookup: old 5 missing targets; fixed 0 (BODY KIT03 -> KIT00).

Fixed 47/47 assertions; skipped repair 6 failures (5 absent model targets).
Parser: missing-header control accepted before, rejected after; 13/13 fixed.
Uses a synthetic partial-kit geometry pack, the production SOLIDMAP loader and
lookup hook, and translated CarPart/model-table lookup. Old loader must fail.
Retail/traffic, authored kits, absent stock LODs, images, CPU and input bytes
remain exact. The parser also rejects malformed inputs without partial repair.
"""
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests/setup'))
from setup_kit import car_convert as cc
import test_car_convert as fixture


def function(source, name):
    match = re.search(r'^[^\n;]*\b' + name + r'\([^\n]*\) \{\n', source, re.M)
    assert match, name
    return source[match.start():source.index('\n}', match.end()) + 2]


HARNESS = r'''
#include "host.h"
#include "funcs.h"
#include "car_kit_fallback.h"
#include <assert.h>
#include <limits.h>
#include <stdarg.h>
uint8_t *G;
typedef struct {
    char name[16], directory[PATH_MAX], geometry[PATH_MAX];
    uint32_t *solid_map;
    unsigned solid_count;
} Addition;
static Addition additions[1], traffic_addition;
static unsigned nadditions=1;
static int traffic_active;
int diag_on;
static _Thread_local Addition *model_namespace;
static unsigned checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"line %d: %s\n",__LINE__,#x); } } while (0)
static uint32_t parts_hash(const char *s) { return car_kit_hash(s); }
static void fail(const char *s) { fprintf(stderr,"%s\n",s); exit(2); }
void diag_event(const char *fmt, ...) { (void)fmt; }
void f_0074D070(Cpu *c) { (void)c; abort(); }
void f_00460C20(Cpu *c) { (void)c; abort(); }
TRANSLATED
PRODUCTION
static uint32_t lookup(const char *name, Addition *a, unsigned lod) {
    // Prehashed model-table entries exercise the real retail instruction path.
    Cpu c={0}; c.esp=0x800000; c.ecx=0x10000;
    c.ebx=0x12345678; c.esi=0x76543210; c.edi=0x89ABCDEF; c.ebp=0xABCDEF89;
    M16(c.ecx+12)=0;
    M32(0x9B09D4)=0x20000; M32(0x20000+0x2C)=0x30000;
    M32(0x30000+4+lod*4)=parts_hash(name);
    M32(c.esp+4)=0; M32(c.esp+8)=lod;
    model_namespace=a;
    f_00751540(&c);
    CHECK(c.esp==0x80000C);
    CHECK(c.ebx==0x12345678 && c.esi==0x76543210 && c.edi==0x89ABCDEF && c.ebp==0xABCDEF89);
    return c.eax;
}
int main(int argc, char **argv) {
    assert(argc==3);
    G=calloc(1,0xA00000); assert(G);
    Addition *a=additions;
    strcpy(a->name,"CAR"); snprintf(a->directory,sizeof a->directory,"%s",argv[1]);
    load_solid_map(a,argv[2]);
    unsigned missing=0;
    for (unsigned lod=0;lod<4;++lod) {
        char donor[64], target[64], stock[64];
        snprintf(donor,sizeof donor,"DONOR_KIT03_BODY_%c",'A'+lod);
        snprintf(target,sizeof target,"NFSAD_CAR_KIT03_BODY_%c",'A'+lod);
        snprintf(stock,sizeof stock,"NFSAD_CAR_KIT00_BODY_%c",'A'+lod);
        uint32_t expected=parts_hash(lod==1?target:stock);
        uint32_t actual=lookup(donor,a,lod);
        if(actual!=expected) ++missing;
        CHECK(actual==expected);
        CHECK(lookup(donor,NULL,lod)==parts_hash(donor)); // retail even with additions active
    }
    uint32_t brake=lookup("DONOR_KITW02_FRONT_BRAKE_A",a,0);
    uint32_t tire=lookup("DONOR_KIT05_FRONT_TIRE_D",a,3);
    missing+=(brake!=parts_hash("NFSAD_CAR_KIT00_FRONT_BRAKE_A"));
    missing+=(tire!=parts_hash("NFSAD_CAR_KIT00_FRONT_TIRE_D"));
    CHECK(brake==parts_hash("NFSAD_CAR_KIT00_FRONT_BRAKE_A"));
    CHECK(tire==parts_hash("NFSAD_CAR_KIT00_FRONT_TIRE_D"));
    CHECK(lookup("DONOR_KITW99_BODY_D",a,3)==parts_hash("NFSAD_CAR_KITW99_BODY_D"));
    CHECK(lookup("DONOR_KIT03_BODY_Z",a,4)==parts_hash("NFSAD_CAR_KIT03_BODY_Z"));
    CHECK(private_solid_hash(a,parts_hash("DONOR_DIFFUSE"))==parts_hash("NFSAD_CAR_DIFFUSE"));
    CHECK(lookup("UNRELATED",a,0)==parts_hash("UNRELATED"));
    Addition verified={0}; strcpy(verified.name,"CAR");
    snprintf(verified.geometry,sizeof verified.geometry,"%s/GEOMETRY.BIN",argv[1]);
    load_solid_map(&verified,argv[2]); // catalog paths take precedence over the legacy directory
    CHECK(lookup("DONOR_KIT03_BODY_A",&verified,0)==parts_hash("NFSAD_CAR_KIT00_BODY_A"));
    free(verified.solid_map);
    nadditions=0;
    CHECK(lookup("DONOR_KIT03_BODY_A",NULL,0)==parts_hash("DONOR_KIT03_BODY_A"));
    // Added-only admission: traffic retains the original map, even in this namespace.
    strcpy(traffic_addition.name,"CAR");
    snprintf(traffic_addition.directory,sizeof traffic_addition.directory,"%s",argv[1]);
    load_solid_map(&traffic_addition,argv[2]);
    CHECK(private_solid_hash(&traffic_addition,parts_hash("DONOR_KIT03_BODY_A"))==parts_hash("NFSAD_CAR_KIT03_BODY_A"));
    free(traffic_addition.solid_map); free(a->solid_map); free(G);
    printf("kit lookup: %u checks, %u failures, missing targets %u/6\n",checks,failures,missing);
    return failures!=0;
}
'''

PARSER_HARNESS = r'''
#include "car_kit_fallback.h"
#include <assert.h>
int main(int argc, char **argv) {
    assert(argc==5);
    FILE *f=fopen(argv[2],"rb"); assert(f);
    uint32_t h[4]; assert(fread(h,1,16,f)==16 && h[3]<=8192);
    uint32_t *map=malloc(h[3]*8), *before=malloc(h[3]*8);
    assert(map && before && fread(map,8,h[3],f)==h[3]); fclose(f);
    memcpy(before,map,h[3]*8);
    f=fopen(argv[1],"rb"); assert(f);
    int result=car_kit_fallback(f,argv[4],map,h[3]); fclose(f);
    unsigned changed=0;
    for (unsigned i=0;i<h[3];++i) {
        assert(map[2*i]==before[2*i]);
        changed+=(map[2*i+1]!=before[2*i+1]);
    }
    int expected=atoi(argv[3]);
    assert(result==expected && changed==(unsigned)(expected>0?expected:0));
    if (result>0) {
        f=fopen(argv[1],"rb"); assert(f);
        assert(car_kit_fallback(f,argv[4],map,h[3])==0); fclose(f);
    }
    free(map); free(before); return 0;
}
'''


def pack(directory):
    raw = fixture.geometry('DONOR', False)
    models = cc.validate_geometry(raw)
    models['DONOR_KIT03_BODY_B'] = models['DONOR_KIT00_BODY_B']
    models['DONOR_KITW99_BODY_D'] = models['DONOR_KIT00_BODY_D']
    renamed, _ = cc.isolate(models, models, 'DONOR', 'CAR', {})
    geometry, _ = cc.write_geometry(raw, renamed)
    (directory / 'GEOMETRY.BIN').write_bytes(geometry)
    requested = [*models, *(f'DONOR_KIT03_BODY_{lod}' for lod in 'ABCD'),
                 'DONOR_KITW02_FRONT_BRAKE_A', 'DONOR_KIT05_FRONT_TIRE_D',
                 'DONOR_KIT03_BODY_Z', 'DONOR_DIFFUSE']
    # Deliberately reproduce installed pre-fix maps, independently of isolate.
    rows = sorted({fixture.nh(n): fixture.nh('NFSAD_CAR' + n[len('DONOR'):]) for n in requested}.items())
    sidecar = struct.pack('<4I', 0x31504D53, 2, fixture.nh('CAR'), len(rows))
    sidecar += b''.join(struct.pack('<II', *row) for row in rows)
    (directory / 'SOLIDMAP.BIN').write_bytes(sidecar)


def parser_checks(tmp):
    raw = (tmp / 'GEOMETRY.BIN').read_bytes()
    index, size = cc.find(raw, 0x134004)
    entries = cc.index(raw)
    def word(offset, value):
        data = bytearray(raw)
        struct.pack_into('<I', data, offset, value)
        return bytes(data)
    # Corrupt the last model after earlier stock names have prepared repairs:
    # a failed decode must never leave half of a mapping repaired.
    last = entries[-1][1]
    header = next((o, z) for c, o, z in cc.children(raw, last + 8, last + entries[-1][2]) if c == 0x134011)
    name_at = ((header[0] + 23) & ~15) + 160
    no_name = bytearray(raw)
    no_name[name_at:header[0] + 8 + header[1]] = b'X' * (header[0] + 8 + header[1] - name_at)
    cases = [('good', raw, 5, 'NFSAD_CAR'), ('foreign-namespace', raw, 0, 'NFSAD_OTHER'),
             ('root-type', word(0, 0), -1, 'NFSAD_CAR'),
             ('root-overflow', word(4, 0xFFFFFFFF), -1, 'NFSAD_CAR'),
             ('short-header', raw[:10], -1, 'NFSAD_CAR'),
             ('short-mesh', raw[:-1], -1, 'NFSAD_CAR'),
             ('index-size', word(index + 4, size - 1), -1, 'NFSAD_CAR'),
             ('index-offset', word(index + 12, len(raw)), -1, 'NFSAD_CAR'),
             ('compressed', word(index + 24, 0x100), -1, 'NFSAD_CAR'),
             ('unordered', word(index + 32, entries[0][0]), -1, 'NFSAD_CAR'),
             ('late-model-type', word(last, 0), -1, 'NFSAD_CAR'),
             ('missing-model-header', word(header[0], 0), -1, 'NFSAD_CAR'),
             ('unterminated-name', bytes(no_name), -1, 'NFSAD_CAR')]
    unit, binary = tmp / 'parser.c', tmp / 'parser'
    unit.write_text(PARSER_HARNESS)
    build = subprocess.run(['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-Iruntime', str(unit), '-o', str(binary)], cwd=ROOT, capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[-3000:]
    for label, data, expected, namespace in cases:
        path = tmp / (label + '.bin')
        path.write_bytes(data)
        run = subprocess.run([str(binary), str(path), str(tmp / 'SOLIDMAP.BIN'), str(expected), namespace],
                             capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
        assert run.returncode == 0, (label, run.stderr[-1500:])
    print(f'geometry parser: {len(cases)}/{len(cases)} controls pass, no partial repairs')


def main():
    paths = [ROOT / 'recomp/out' / f'speed_{number}.c' for number in (46, 47)]
    if not all(path.is_file() for path in paths):
        print('SKIP: build translated functions first')
        return 3
    translated = '\n'.join(function(path.read_text(), name) for path, name in
                           zip(paths, ('f_007397D0', 'f_00751540_orig')))
    source = (ROOT / 'runtime/car85.c').read_text()
    names = ('load_solid_map', 'private_solid_hash', 'model_00751540', 'f_00751540')
    production = '\n'.join(function(source, name) for name in names)
    old = production.replace('int repaired=car_kit_fallback(models,space,a->solid_map,a->solid_count);', 'int repaired=0;')
    assert old != production
    with tempfile.TemporaryDirectory(prefix='car-kit-') as td:
        tmp = Path(td)
        pack(tmp)
        inputs = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp.glob('*.BIN')}
        for label, code in (('fixed', production), ('old-lookup', old)):
            unit, binary = tmp / 'test.c', tmp / label
            unit.write_text(HARNESS.replace('TRANSLATED', translated).replace('PRODUCTION', code))
            build = subprocess.run(['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                                    '-fsanitize=address,undefined', '-Iruntime', '-Irecomp', '-Irecomp/out',
                                    str(unit), '-o', str(binary)], cwd=ROOT, capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-3000:]
            run = subprocess.run([str(binary), str(tmp), str(tmp / 'SOLIDMAP.BIN')],
                                 capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
            print(label, run.stdout.strip())
            assert run.returncode == (0 if label == 'fixed' else 1), run.stderr[-3000:]
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in inputs.items())
        print('input geometry and SOLIDMAP digests unchanged')
        parser_checks(tmp)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
