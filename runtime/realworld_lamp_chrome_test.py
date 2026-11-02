#!/usr/bin/env python3
"""Physical chrome leaves lamp units on the retail car shader.

Stock lamp units draw the CHROME light material with the car's lamp-state
texture (geometry slot HEADLIGHT_LEFT/RIGHT or BRAKELIGHT_LEFT/RIGHT, replaced
per car by <base>_ON for headlights and <base>_OFF/_ON for brakelights). The
retail CHROME record has DiffuseMin = DiffuseMax = 1, so that texture is the lamp's
look; zero-diffuse conductor shading discards it and leaves a grey/dark unit.

Fixture: every stock car's LOD A undamaged parts, decoded from GEOMETRY.BIN.
Lamp texture hashes come from the GetUsedCarTextureInfo derivation (base name +
suffix, chained string hash), each checked present in the car's TEXTURES.BIN.
The production recorder mat_used_car_textures (materials.m, called by the
GetUsedCarTextureInfo hook in car85.c) runs on a fake UsedCarTextureInfo;
the production realworld_materials.c routes each chrome draw under All.

Numbers (lamp-chrome triangle draws routed to the physical shader, brakelights
counted in both states / other chrome triangles kept physical): this code
0 / 61411; original code (no lamp registry: REALWORLD_MATERIALS_SOURCE and
MATERIALS_SOURCE/CAR85_SOURCE pointed at the pre-fix files) 12021 / 61411. 27 cars,
10199 distinct lamp-chrome triangles.
Off routes 0 draws in both.

Ambient occlusion: the retail car VS takes saturate(2 x) of the vertex colour
(PC data: 0.5 is unoccluded) for its env and specular terms. The production
physical-chrome AO expression runs on every decoded chrome vertex: this code
0 vertices differ from retail; the original raw saturate(x) differs on 51285 of
72262 (body chrome reflections at about half strength; rims are 255, unaffected).
Interior: the VS must clamp per vertex and the pixel stage use the interpolated value (retail order). Four barycentric
samples on each of 71610 chrome triangles: this code 0 differ; clamping after interpolation (pixel-stage
saturate(2 x)) differs on 9418, e.g. COBALTSS_KIT00_FRONT_TIRE_A MAGCHROME [214,12,234] centroid 1.0 vs retail 0.698.
Exit 3 without the stock game folder.
"""
import ctypes as ct
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
MODULE_PATH = Path(os.environ.get('REALWORLD_MATERIALS_SOURCE', RT / 'realworld_materials.c'))
SRC = Path(os.environ.get('MATERIALS_SOURCE', RT / 'materials.m')).read_text()
CAR85 = Path(os.environ.get('CAR85_SOURCE', RT / 'car85.c')).read_text()
CARS = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'CARS'
WANT_LAMP, WANT_OTHER = 12021, 61411
MODULE = MODULE_PATH.read_text()


def bhash(s, h=0xFFFFFFFF):
    for ch in s.encode():
        h = (h * 33 + ch) & 0xFFFFFFFF
    return h


def function(s, name):
    m = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^{};]*?\) \{', s, re.M)
    if not m:
        return None
    start = s.index('{', m.start()); depth = 0
    for t in re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|[{}]', s[start:], re.S):
        if t[0] == '{': depth += 1
        elif t[0] == '}':
            depth -= 1
            if not depth: return s[m.start():start + t.end()]
    raise AssertionError(name)


HARNESS = r'''
uint8_t *G;
int x360_post_rolloff(void) { return 0; }
static uint32_t suffix[6];
HOOK
void used(const uint32_t *h) {             // GetUsedCarTextureInfo's lamp fields, then the recorder
    if (!G) G = calloc(1, 65536);
    memcpy(suffix, h, sizeof suffix);
    for (int i = 0; i < 3; ++i) { M32(0x1000 + 0x340 + 4 * i) = suffix[i]; M32(0x1000 + 0x358 + 4 * i) = suffix[3 + i]; }
    CALL
}
int route(int mode, uint32_t material, uint32_t texture) {
    realworld_materials_set(mode);
    realworld_materials_capture(material, 1, 0);
    realworld_materials_texture(texture);
    return realworld_materials_draw(0x89E9A52CCEE09BCFULL, 0x1BEA38904139377AULL) != 0;
}
'''


def main():
    if not CARS.is_dir():
        print('SKIP: needs the stock game CARS folder')
        return 3
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    import carbon_car as cc
    saturate = lambda v: min(max(v, 0.0), 1.0)
    env = {'saturate': saturate}
    # Vertex stage writes texcoord6.w; the pixel stage reads the interpolated value as ao.
    vs_expr = re.search(r'texcoord6=float4\(si\.v\[0\]\.xyz,([^;]+)\);', MODULE)[1].replace('2.0f', '2.0').replace('si.v[2].x', 'x')
    ps_expr = re.search(r'float ao=([^;]+);', MODULE)[1].replace('2.0f', '2.0').replace('pi.texcoord6.w', 'w')
    vertex_ao = eval('lambda x: ' + vs_expr, env)
    pixel_ao = eval('lambda w: ' + ps_expr, env)
    ao_bad = ao_all = tri_bad = tri_all = 0
    example = None
    samples = ((1/3, 1/3, 1/3), (.5, .25, .25), (.25, .5, .25), (.25, .25, .5))
    hook = function(SRC, 'mat_used_car_textures')
    # The guest hook must hand every GetUsedCarTextureInfo result (after added-car remapping) to the recorder.
    wired = 'mat_used_car_textures(used);' in (function(CAR85, 'f_00751860') or '')
    if not wired:
        hook = None
    with tempfile.TemporaryDirectory(prefix='lamp-chrome-') as td:
        tmp = Path(td)
        code = '#include <stdlib.h>\n#include "host.h"\n#include "' + str(RT / 'traffic_detail.c') + '"\n#include "' + str(MODULE_PATH) + '"\n'
        code += HARNESS.replace('HOOK', hook or '').replace('CALL', 'mat_used_car_textures(0x1000);' if hook else '')
        (tmp / 'lamp.c').write_text(code)
        subprocess.run(['clang', '-dynamiclib', '-I', RT, '-I', ROOT / 'recomp', tmp / 'lamp.c', '-o', tmp / 'lamp.dylib'], check=True)
        lib = ct.CDLL(str(tmp / 'lamp.dylib'))
        lib.route.argtypes = [ct.c_int, ct.c_uint32, ct.c_uint32]
        chrome = {bhash(n) for n in ('CHROME', 'MAGCHROME', 'USER_RIMS_CHROME')}
        keys = {bhash('HEADLIGHT_LEFT'): 0, bhash('HEADLIGHT_RIGHT'): 0, bhash('BRAKELIGHT_LEFT'): 1, bhash('BRAKELIGHT_RIGHT'): 1}
        lamp_phys = other_phys = other = off = lamp_cars = 0
        for car_dir in sorted(p for p in CARS.iterdir() if (p / 'GEOMETRY.BIN').is_file()):
            car = car_dir.name
            bases = [bhash(car + '_KIT00_HEADLIGHT'), bhash(car + '_KIT00_BRAKELIGHT')]
            sfx = [bhash(s, b) for b in bases for s in ('_OFF', '_ON', '_DAMAGE0')]
            lib.used((ct.c_uint32 * 6)(*sfx))
            # Retail states: headlights draw <base>_ON (UpdateLightStateTextures), brakelights _OFF or _ON.
            states = ([sfx[1]], [sfx[3], sfx[4]])
            textures = (car_dir / 'TEXTURES.BIN').read_bytes() if (car_dir / 'TEXTURES.BIN').is_file() else b''
            car_lamp = 0
            for _, solid in cc.solids((car_dir / 'GEOMETRY.BIN').read_bytes()):
                try:
                    name = cc.payload(solid, 0x134011, 16)[160:].split(b'\0')[0].decode()
                    if not name.endswith('_A') or 'DAMAGE' in name:
                        continue
                    model = cc.native_wheel_model(solid)
                except Exception:
                    continue
                for r in model['records']:
                    material = model['lights'][r[29]]
                    if material not in chrome:
                        continue
                    tris = cc.u32(r, 92) // 3
                    first = cc.u32(r, 68)
                    raw = model['vertices'].tobytes()
                    idx = [int(i) for i in model['indices'][first:first + tris * 3]]
                    for v in set(idx):
                        x = raw[v * 36 + 26] / 255.0          # D3DCOLOR red = v2.x
                        ao_all += 1
                        ao_bad += abs(pixel_ao(vertex_ao(x)) - saturate(2.0 * x)) > 1e-6
                    for t in range(tris):
                        xs = [raw[v * 36 + 26] / 255.0 for v in idx[3 * t:3 * t + 3]]
                        tri_all += 1
                        diff = 0.0
                        for b in samples:       # retail: clamp per vertex, then interpolate
                            want = sum(bi * saturate(2.0 * xi) for bi, xi in zip(b, xs))
                            got = pixel_ao(sum(bi * vertex_ao(xi) for bi, xi in zip(b, xs)))
                            diff = max(diff, abs(got - want))
                        tri_bad += diff > 1e-6
                        if (car, name, material) == ('COBALTSS', 'COBALTSS_KIT00_FRONT_TIRE_A', bhash('MAGCHROME')) and \
                                [round(xi * 255) for xi in xs] == [214, 12, 234]:
                            example = (round(pixel_ao(sum(vertex_ao(xi) for xi in xs) / 3), 3),
                                       round(sum(saturate(2.0 * xi) for xi in xs) / 3, 3))
                    slot = model['textures'][r[24]] if r[24] < len(model['textures']) else 0
                    if slot in keys:
                        for t in states[keys[slot]]:
                            assert struct.pack('<I', t) in textures, (car, name, 'lamp texture hash absent from TEXTURES.BIN')
                            lamp_phys += tris * lib.route(3, material, t)
                            off += lib.route(0, material, t)
                        car_lamp += tris
                    else:
                        other += tris
                        other_phys += tris * lib.route(3, material, slot)
                        off += lib.route(0, material, slot)
            lamp_cars += car_lamp > 0
        # Brakelight triangles count once per retail state (_OFF and _ON).
        print(f'lamp chrome: {lamp_cars} cars, {lamp_phys} lamp-state triangle draws on the physical shader (want 0, '
              f'original {WANT_LAMP}); other chrome {other_phys}/{other} physical (want {WANT_OTHER}); Off routes {off}')
        print(f'chrome AO: {ao_bad}/{ao_all} vertices differ from the retail saturate(2 x) (want 0)')
        print(f'chrome AO interior: {tri_bad}/{tri_all} triangles differ from retail clamp-then-interpolate at 4 barycentric '
              f'samples (want 0); COBALTSS_KIT00_FRONT_TIRE_A [214,12,234] centroid {example} (want retail 0.698)')
        if lamp_phys or other_phys != WANT_OTHER or other != WANT_OTHER or off or ao_bad or tri_bad or example is None:
            print('FAIL')
            return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
