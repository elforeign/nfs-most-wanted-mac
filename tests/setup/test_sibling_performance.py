"""Stock sibling ordering through the translated MW PerfStats::Fetch.

722 top-speed bar: donor alias .58974 (retail SLR .58974, exit 1),
plain Undercover record .34826 (exit 1), scaled sibling record .60722.
The test uses the runtime reader, stock layouts and retail rating bounds.
"""
import ctypes as ct
from contextlib import closing
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'recomp'), str(Path(__file__).resolve().parent)]
from recomp import PE
from setup_kit import car_performance, car_sources, car_x360_inputs, ps_attrib, uc_attrib
from setup_kit.car_convert import FitAttributes, hash32, wheel_sidecar
from setup_kit.input_paths import input_path
from setup_kit.x360 import Source
from test_car_performance import FIELDS, Record, layout, ref

# Independent roster census. Arrows identify a retail sibling even when
# the catalog's model/parts donor is a different car.
PAIRS = {
    'SLR': ('MER_SLR_722_07',),
    'MURCIELAGO': ('MURCIELAGO640',),
    'GALLARDO': ('LAM_GAL_560_09', 'LAM_GAL_SUP_08'),
    'CORVETTE': ('CORVETTEZ06',),
    '997S': ('997GT3RS', '997GT3', '997GT2'),
    '911TURBO': ('997TT',),
    'LANCEREVO8': ('LANCEREVO9', 'LANCEREVOX'),
    'ECLIPSEGT': ('ECLIPSE',),
    'MUSTANGGT': ('MUSTANGSHLBYN', 'FOR_SHL_GT_08', 'FOR_SHL_TER_08', 'MUSTANG03'),
    'GTI': ('R32',),
    'CTS': ('CTSV',),
    'IS300': ('IS350', 'LEX_IS_F_08'),
    'A4': ('S4', 'RS4'),
    'A3': ('S3',),
    'TT': ('TTN',),
    'VIPER': ('DOD_VIP_SRT_08',),
}
SLOWER = {'ECLIPSE', 'MUSTANG03'}
EQUAL_SPEED = {'LANCEREVOX', 'FOR_SHL_TER_08', 'S3', 'S4', 'RS4', 'TTN'}

# The native stock estimator samples these layouts. Supply only the retail
# Attrib instance/ref accessors and stock upgrade state it needs, then run its
# translated integration, shifts, torque interpolation and induction code.
HARNESS = r'''
#include "runtime.h"
#include <assert.h>
#include <string.h>

uint8_t *G;
static uint8_t memory[0xA00000];
enum {
    VEHICLE=0x1000, BODY=0x1100, HANDLING=0x1200, ENGINE=0x1300,
    INDUCTION=0x1400, TRANSMISSION=0x1500, CHASSIS=0x1600, TIRES=0x1700,
    NOS=0x1800, DEFAULT=0x1900, STATS=0x2000, STACK=0x3000
};

void f_00454810(Cpu *c) {
    assert(c->ecx==VEHICLE);
    switch (M32(c->esp+4)) {
        case 0xf1f5fbc7: c->eax=ENGINE; break;
        case 0xc92a0142: c->eax=INDUCTION; break;
        case 0x07a7a3e5: c->eax=TRANSMISSION; break;
        case 0xafa210f0: c->eax=CHASSIS; break;
        case 0xbd38d1ca: c->eax=TIRES; break;
        case 0xb1669f64: c->eax=NOS; break;
        case 0x36350867: c->eax=DEFAULT; break;
        case 0xaacbe2e7: c->eax=HANDLING+4*M32(c->esp+8); break;
        default: assert(0);
    }
    c->esp+=12;
}
static void construct(Cpu *c) {
    M32(c->ecx+8)=M32(c->esp+4);
    c->eax=c->ecx;
    c->esp+=16;
}
void f_00407490(Cpu *c) { construct(c); }
void f_00407510(Cpu *c) { construct(c); }
void f_00407630(Cpu *c) { construct(c); }
void f_004AB2C0(Cpu *c) { construct(c); }
void f_004AB5D0(Cpu *c) { construct(c); }
void f_0066B970(Cpu *c) { construct(c); }
void f_0066BA60(Cpu *c) { construct(c); }
void f_00452940(Cpu *c) { c->eax=M16(c->ecx+2); c->esp+=4; }
void f_0045A430(Cpu *c) { c->esp+=4; }
void f_006269B0(Cpu *c) { c->eax=DEFAULT; c->esp+=4; }
void f_00677F80(Cpu *c) { x87_push(c,0); c->esp+=4; }
void f_007C4B80(Cpu *c) {
    int32_t value=(int32_t)c->st[c->top];
    c->eax=(uint32_t)value;
    c->edx=value<0 ? UINT32_MAX : 0;
    x87_pop(c);
    c->esp+=4;
}

TRANSLATED

void stock_constants(const void *data,uint32_t address,uint32_t size) {
    assert(address<=sizeof memory && size<=sizeof memory-address);
    memcpy(memory+address,data,size);
}

void stock_stats(const char *body,const char *handling,const char *engine,
                 const char *transmission,const char *induction,const char *chassis,
                 const char *tires,const char *nos,float *out) {
    G=memory;
    M32(VEHICLE+8)=BODY;
    memcpy(G+BODY,body,0x50);
    memcpy(G+HANDLING,handling,8);
    memcpy(G+ENGINE,engine,0x60);
    memcpy(G+TRANSMISSION,transmission,0x84);
    memcpy(G+INDUCTION,induction,0x1c);
    memcpy(G+CHASSIS,chassis,0x74);
    memcpy(G+TIRES,tires,0x50);
    memcpy(G+NOS,nos,0x20);
    Cpu c={.ecx=STATS,.esp=STACK,.cw=0x37f};
    M32(STACK)=0;
    M32(STACK+4)=VEHICLE;
    M32(STACK+8)=M32(STACK+12)=0;
    f_00678CE0_orig(&c);
    assert(c.esp==STACK+16);
    memcpy(out,G+STATS,12);
}
'''


def native_library(work, exe):
    functions = ('00401AE0', '0066BC60', '0066BCC0', '0066BF20', '0066C3A0',
                 '0066C8A0', '00672FE0', '00673400', '00678CE0_orig')
    pattern = re.compile(r'^void f_(' + '|'.join(functions) + r')(?:_orig)?\(Cpu.*?^}', re.M | re.S)
    source = {}
    for shard in (0, 33, 34, 35, 36):
        path = ROOT / f'recomp/out/speed_{shard:02d}.c'
        if path.is_file():
            for function in pattern.finditer(path.read_text()):
                # Retail siblings use the untouched original torque consumer.
                source[function[1]] = function[0].replace(
                    f'void f_{function[1]}_orig(', f'void f_{function[1]}(', 1)
    assert source.keys() == set(functions), set(functions) - source.keys()
    declarations = '\n'.join('void f_' + name + '(Cpu *);' for name in functions)
    harness = work / 'stock_stats.c'
    harness.write_text(HARNESS.replace('TRANSLATED', declarations + '\n' + '\n'.join(source.values())))
    library = work / 'stock_stats.dylib'
    subprocess.run(['clang', '-std=c11', '-Wno-parentheses-equality', '-dynamiclib', '-I', str(ROOT / 'recomp'),
                    str(harness), str(ROOT / 'runtime/car_performance.c'), '-o', str(library)], check=True)
    lib = ct.CDLL(str(library))
    lib.stock_constants.argtypes = [ct.c_void_p, ct.c_uint32, ct.c_uint32]
    image = PE(exe)
    for name, _, address, size, offset in image.sections:
        if name.rstrip(b'\0') in (b'.rdata', b'.data'):
            lib.stock_constants(image.data[offset:offset + size], image.base + address, size)
    lib.stock_stats.argtypes = [ct.c_void_p] * 8 + [ct.POINTER(ct.c_float)]
    lib.car_performance_read.argtypes = [ct.c_int, ct.c_uint32, ct.c_uint32, ct.POINTER(Record)]
    lib.car_performance_body.argtypes = [ct.POINTER(Record), ct.c_void_p, ct.c_void_p]
    lib.car_performance_layout.argtypes = [ct.POINTER(Record), ct.c_uint, ct.c_int,
                                          ct.c_void_p, ct.c_void_p, ct.c_void_p]
    lib.car_performance_clamp_top_speed.argtypes = [ct.c_void_p, ct.c_float]
    lib.car_performance_clamp_top_speed.restype = ct.c_float
    return lib


def bars(lib, native, donor, *, record=None, wheels=None):
    key = hash32(donor.lower())
    body = ct.create_string_buffer(layout(native, 'pvehicle', key, 0x50))
    handling = ct.create_string_buffer(bytes(8) + native.raw('pvehicle', key, 'HandlingRating'))
    if record is not None:
        lib.car_performance_body(ct.byref(record), body, handling)
    lengths = {cls: size for cls, size, _ in FIELDS} | {'nos': 0x20}
    layouts = {}
    for index, cls in enumerate((*[cls for cls, *_ in FIELDS], 'nos')):
        original = layout(native, cls, ref(native, key, cls, '<'), lengths[cls])
        projected = ct.create_string_buffer(original)
        if record is not None and (record.version == 3 or cls != 'nos'):
            lib.car_performance_layout(ct.byref(record), index, False, original, original, projected)
        layouts[cls] = projected
    if wheels and len(wheels) == 380:
        for index, offset in enumerate((0x30, 0x40, 0x28)):
            layouts['tires'][offset:offset+8] = wheels[196+index*8:204+index*8]

    raw = (ct.c_float * 3)()
    lib.stock_stats(body, handling.raw[8:], *(layouts[cls] for cls in
                    ('engine', 'transmission', 'induction', 'chassis', 'tires', 'nos')), raw)
    time, speed, grip = raw
    if record is not None and record.source:
        speed = lib.car_performance_clamp_top_speed(layouts['engine'], speed)
    # Retail bounds from the default stock roster (PerfStats::Init, 00683EB0).
    return tuple(max(0.0, min(1.0, v)) for v in
                 ((speed - 73.743286) / (110.742058 - 73.743286),
                  (grip - 36) / 64, (11.5 - time) / 10))


def read_record(lib, work, data, car, donor):
    record = Record()
    with (work / 'PERFORMANCE.BIN').open('w+b') as stream:
        stream.write(data)
        stream.flush()
        assert lib.car_performance_read(stream.fileno(), hash32(car.lower()),
                                        hash32(donor.lower()), ct.byref(record)) == 0, car
    return record


def check_order(car, added, retail):
    speed, handling, acceleration = (new - old for new, old in zip(added, retail))
    if car in EQUAL_SPEED:
        # The same rated top speed can differ slightly after MW's discrete
        # integration, which also depends on mass and time to enter top gear.
        assert abs(speed) < 0.005, car
    elif car in SLOWER:
        assert speed < 0, car
    else:
        assert speed > 0, car
    assert handling < 0 if car == 'MUSTANG03' else handling > 0, car
    if car in SLOWER | {'LANCEREVOX'}:
        assert acceleration <= 0, car
    else:
        assert acceleration >= 0, car


def main(control):
    mw = Path(os.environ.get('NFS_GAME') or ROOT.parent / 'Need For Speed Most Wanted Black Edition')
    exe = Path(os.environ.get('NFS_SPEED_EXE') or mw / 'speed.exe')
    paths = {
        'mw': mw / 'GLOBAL/attributes.bin',
        'carbon': input_path('NFSC/GLOBAL/attributes.bin'),
        'prostreet': input_path('Need for Speed ProStreet/GLOBAL/ATTRIBUTES.BIN'),
        'undercover': input_path('Need for Speed Undercover/GLOBAL/ATTRIBUTES.BIN'),
    }
    x360 = next(input_path('NFSC_X360').glob('*.iso'), None)
    if not x360 or not all(path.is_file() for path in (*paths.values(), exe, ROOT / 'recomp/out/speed_35.c')):
        print('SKIP: sibling performance needs the source attributes and translated MW')
        return 3
    inputs = {game: path.read_bytes() for game, path in paths.items()}
    with closing(Source(x360)) as disc:
        logical = car_sources._logical(disc, car_sources._physical(disc))
        console = car_sources._read(disc, logical[car_sources.path_hash('GLOBAL/ATTRIBUTES.BIN')])
    native = FitAttributes(inputs['mw'])
    expected = {car: sibling for sibling, cars in PAIRS.items() for car in cars}
    roster = [row for row in json.loads((ROOT / 'tools/setup_kit/car_selections.json').read_bytes())
              if row['origin']['folder'] in expected]
    assert {row['origin']['folder'] for row in roster} == expected.keys() == car_performance.SIBLINGS.keys()
    assert {row['origin']['folder'] for row in roster if row['origin']['platform'] == 'x360'} == {
        '997GT3RS', '997TT', 'CORVETTEZ06', 'R32',
    }

    with tempfile.TemporaryDirectory(prefix='sibling-performance-') as td:
        work = Path(td)
        lib = native_library(work, exe)
        retail = {sibling: bars(lib, native, sibling) for sibling in PAIRS}
        car, donor = 'MER_SLR_722_07', 'SLR'
        plain = read_record(lib, work, uc_attrib.source_performance(
            inputs['undercover'], inputs['mw'], car, donor), car, donor)
        plain.source = 2
        if control:
            added = retail[donor] if control == '--donor-only' else bars(lib, native, donor, record=plain)
            print(f'{control}: 722 {added[0]:.5f}, SLR {retail[donor][0]:.5f}', flush=True)
            return int(added[0] <= retail[donor][0])

        assert lib.car_performance_enabled()
        assert abs(retail['SLR'][0] - 0.58974) < 0.00002
        assert abs(bars(lib, native, donor, record=plain)[0] - 0.34826) < 0.00002
        result = {}
        for row in roster:
            origin = row['origin']
            game, platform, car = origin['game'], origin['platform'], origin['folder']
            donor = row['donors']['model']
            wheels = None
            if game == 'carbon':
                attributes = (car_x360_inputs.normalize_attributes(console, car)
                              if platform == 'x360' else inputs[game])
                wheels = wheel_sidecar(attributes, car, donor, fit=True)
                data = car_performance.sibling_record(native, car, donor, wheels=wheels)
            elif game == 'prostreet':
                sidecars = ps_attrib.sidecars(inputs[game], inputs['mw'], car, donor)
                data, wheels = sidecars['PERFORMANCE.BIN'], sidecars['WHEELFIT.BIN']
            else:
                data = uc_attrib.performance(inputs[game], inputs['mw'], car, donor)
            record = read_record(lib, work, data, car, donor)
            assert record.version == 3
            added = bars(lib, native, donor, record=record, wheels=wheels)
            print(f'{car} {platform}: ' + ' / '.join(f'{v:.5f}' for v in added), flush=True)
            check_order(car, added, retail[expected[car]])
            if platform == 'pc':
                result[car] = added
        assert abs(result['MER_SLR_722_07'][0] - 0.60722) < 0.00002
        assert result['LAM_GAL_560_09'][0] > result['LAM_GAL_SUP_08'][0] > retail['GALLARDO'][0]
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
