"""Port car normal maps (NFS_CAR_NORMALMAPS=port): four family checks, each binding + content, on the production selector,
generator and upload, from the PC files alone (exit 3 without them).
  Binding (carnm_find, no console maps loaded): port binds TREAD, TREADR, DOORLINE and leaves DAMAGE0 on PC shading;
    today's route (x360 without console files) binds 0/4 port maps; with console sentinels loaded the console map wins 4/4.
  Content, against the PC diffuse alone (r = correlation of the map's (x, y) with the diffuse's downhill gradient):
    TREAD r .60 on its own diffuse; TREADR r .55, and TREAD's map on TREADR's diffuse r -.01 (alignment fails); DOORLINE
    r .31 with the alpha stroke's uphill gradient, 0 tilted padding texels, 116/116 stroke rows with opposite-facing walls;
    DAMAGE0 slope .0059 < one 8-bit step .0078 (neutral, no map).
  Deleting one recipe from the generator fails exactly that family's check (4 runs, 1 failure each).
  GPU, production car paint shader with the normal-map path on, BGRA8 512x256: changed pixels port map vs flat map
    (NFS_TEST_CARNM_FLAT=1) TREAD 51,860 / TREADR 28,216 / DOORLINE 48,192; the flat-vs-flat control 0.
Old route 0/4 families pass, new 4/4.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np

RT = Path(__file__).resolve().parent
ROOT = RT.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tools_snapshot'))
sys.path.insert(0, str(RT))
import car_normals_port as gen  # noqa: E402
import x360_shader_lits as lits  # noqa: E402
from materials_stub_test import function  # noqa: E402

FAMILIES = gen.FAMILIES


def run(args, expected=0, **kw):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)
    assert p.returncode == expected, p.stdout[-2000:] + p.stderr[-5000:]
    return p


def parse(text):
    """{family: (w, h, xy float array)} and the DAMAGE0 slope from a port_car_normals.inc."""
    arrays = {m.group(1): np.array([int(v) for v in m.group(3).replace('\n', '').split(',') if v.strip()], np.float64) / 32767
              for m in re.finditer(r'static const int16_t car_normals_port_(\w+)\[(\d+)\] = \{(.*?)\};', text, re.S)}
    maps = {}
    for fam, w, h, arr in re.findall(r'\{"(\w+)", (\d+), (\d+), (\w+)\}', text):
        if arr != '0':
            maps[fam] = (int(w), int(h), arrays[arr[len('car_normals_port_'):]].reshape(int(h), int(w), 2))
    slope = re.search(r'#define CAR_NORMALS_PORT_DAMAGE0_SLOPE ([0-9.]+)f', text)
    return maps, float(slope.group(1)) if slope else 0.0


def downhill(img, w, h):
    """Upscaled image's (-d/dx, -d/dy), wrapped central differences (image units)."""
    s = w // img.shape[1]
    a = np.kron(img, np.ones((s, s)))
    return -(np.roll(a, -1, 1) - np.roll(a, 1, 1)), -(np.roll(a, -1, 0) - np.roll(a, 1, 0))


def corr(xy, gx, gy):
    a, b = xy.reshape(-1, 2), np.stack([gx, gy], 2).reshape(-1, 2)
    return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum() + 1e-30))


def content(maps, slope, diff):
    """Per-family content verdicts (True = pass) and the numbers behind them."""
    ok, note = {}, {}
    for fam in ('TREAD', 'TREADR'):
        if fam not in maps:
            ok[fam], note[fam] = False, 'no map'
            continue
        w, h, xy = maps[fam]
        lum = diff[fam][..., :3].mean(2)
        r = corr(xy, *downhill(lum, w, h))
        other = 'TREADR' if fam == 'TREAD' else 'TREAD'
        swap = corr(maps[other][2], *downhill(lum, w, h)) if other in maps and maps[other][:2] == (w, h) else 0.0
        tilted = int((np.hypot(xy[..., 0], xy[..., 1]) > np.sin(np.radians(5))).sum())
        ok[fam] = r > 0.3 and tilted > 0.05 * w * h
        note[fam] = 'r %.2f, %s map on its diffuse r %.2f, %d tilted texels' % (r, other, swap, tilted)
        if fam == 'TREADR':
            ok[fam] = ok[fam] and swap < 0.15                # the TREAD layout must not pass for TREADR
    if 'DOORLINE' in maps:
        w, h, xy = maps['DOORLINE']
        alpha = diff['DOORLINE'][..., 3]
        gx, gy = downhill(alpha, w, h)
        r = corr(xy, -gx, -gy)                               # recess at the stroke peak: walls face away from it
        s = w // alpha.shape[1]
        pad = np.kron(alpha, np.ones((s, s))) <= 0
        bad_pad = int((np.abs(xy[pad]) > 0).sum())
        stroke = [y for y in range(h) if alpha[y // s, :16].max() >= 0.25]     # first treatment's rows with a stroke
        rows = sum(1 for y in stroke if (xy[y, :16 * s, 0] > 0.2).any() and (xy[y, :16 * s, 0] < -0.2).any())
        ok['DOORLINE'] = r > 0.2 and bad_pad == 0 and stroke and rows == len(stroke)
        note['DOORLINE'] = 'r %.2f, %d tilted padding texels, %d/%d stroke rows with both walls' % (r, bad_pad, rows, len(stroke))
    else:
        ok['DOORLINE'], note['DOORLINE'] = False, 'no map'
    ok['DAMAGE0'] = 'DAMAGE0' not in maps and 0 < slope < gen.NORMAL8_STEP
    note['DAMAGE0'] = 'slope %.4f, 8-bit step %.4f, map %s' % (slope, gen.NORMAL8_STEP, 'DAMAGE0' in maps)
    return ok, note


ROUTE = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
#include "car_normals_port.h"
#define X360_SHADER_LITS 1
uint8_t *G; int diag_on;
void diag_event(const char *s, ...) { (void)s; }
static uint32_t carnm_x360_d3d[4];
static const struct { const char *diffuse; } carnm_x360[] = {{"TREAD"}, {"TREADR"}, {"DOORLINE"}, {"DAMAGE0"}};
static uint32_t ti_d3d(uint32_t ti) { return ti ? 0x12345678 : 0; }
static uint32_t bhash(const char *s) { uint32_t h = ~0u; while (*s) h = h * 33 + (unsigned char)*s++; return h; }
uint32_t call_guest(Cpu *c, uint32_t f, int n, const uint32_t *a) { (void)c; (void)f; (void)n; (void)a; return 0; }
@FUNCTIONS@
int main(int argc, char **argv) {
    G = calloc(1, 0xA00000); Cpu c = {0};
    if (argc > 1) for (int i = 0; i < 4; ++i) carnm_x360_d3d[i] = 0xC0DE0000u + i;   // console maps loaded (sentinels)
    M32(0x982CB4) = M32(0x982CB8) = 0x1000;
    for (int i = 0; i < 4; ++i) {
        strcpy((char *)G + 0x100C, carnm_x360[i].diffuse);
        carnm_find(&c);
        printf("%s %u %d\n", carnm_x360[i].diffuse, carnm_tex, mat_carnm_port());
    }
    return 0;
}
'''


def route(d, material):
    fns = ['carnm_on', 'materials_set_car_normals']
    body = '\n'.join(function(material, n) for n in fns)
    decl = material[material.index('static char carnm_name[32];'):material.index('uint32_t mat_carnm_texture(void)')]
    decl += 'int mat_carnm_port(void) { return carnm_port; }\n'
    find = material[material.index('static void carnm_find('):]
    find = find[:find.index('\n}\n') + 3]
    (d / 'route.c').write_text(ROUTE.replace('@FUNCTIONS@', 'static int carnm = -1;\n' + body + '\n' + decl + find))
    run(['clang', '-x', 'objective-c', '-fobjc-arc', '-I', RT, '-I', ROOT / 'recomp', '-I', ROOT / 'recomp/out',
         d / 'route.c', RT / 'car_normals_port.m', '-framework', 'Metal', '-framework', 'Foundation', '-o', d / 'route'])
    out = {}
    for value in ('pc', 'x360', 'port'):
        for console in (0, 1):
            env = dict(os.environ, NFS_CAR_NORMALMAPS=value)
            rows = run([d / 'route'] + (['console'] if console else []), env=env).stdout.split('\n')
            out[value, console] = {r.split()[0]: (int(r.split()[1]), int(r.split()[2])) for r in rows if r}
    return out


HARNESS = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <string.h>
#include "car_normals_port.h"
int main(int argc, const char **argv) { @autoreleasepool {
    id<MTLDevice> mtl = MTLCreateSystemDefaultDevice(); if (!mtl) { fprintf(stderr, "SKIP Metal device\n"); return 3; }
    NSString *body = [NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:nil];
    int family = atoi(argv[2]);
    NSString *vs = @"\nvertex In nm_vs(uint id [[vertex_id]]) {\n"
        "float2 p=float2((id<<1)&2,id&2); In o; o.pos=float4(p*2-1,.5,1);\n"
        "o.color0=float4(.25,.3,.35,1);o.color1=float4(.8,.8,.8,.25);\n"
        "o.texcoord0=float4(0,0,1,0);o.texcoord1=float4(p,0,0);o.texcoord2=float4(.5,.5,.5,1);\n"
        "o.texcoord3=float4(normalize(float3(.6,.3,.75)),1);o.texcoord4=float4(0,0,1,0);\n"
        "o.texcoord5=float4(normalize(float3(.1,.1,1)),1);o.texcoord6=float4(p*.2,.008,1);\n"
        "o.texcoord7=float4(0);return o;}\n";
    NSString *source = [@"#define MAT_VARIANT 2\n#define MAT_SHADOW_HW 0\n#define MAT_CAR_HDR 1\n#define MAT_PAINT_FINITE_AB 0\n#define MAT_PAINT_NAN_TINT 0\n" stringByAppendingString:[body stringByAppendingString:vs]];
    NSError *err = nil; id<MTLLibrary> lib = [mtl newLibraryWithSource:source options:nil error:&err];
    if (!lib) { fprintf(stderr, "MSL %s\n", err.localizedDescription.UTF8String); return 1; }
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"nm_vs"]; pd.fragmentFunction = [lib newFunctionWithName:@"sm_main"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    id<MTLRenderPipelineState> pso = [mtl newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!pso) { fprintf(stderr, "PSO %s\n", err.localizedDescription.UTF8String); return 1; }
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:512 height:256 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget; td.storageMode = MTLStorageModeShared;
    id<MTLTexture> target = [mtl newTextureWithDescriptor:td];
    id<MTLTexture> textures[4];
    const float values[4][4] = {{.15,.3,.5,1},{.2,.3,.4,1},{.75,.75,.75,1},{.5,.5,.5,1}};
    for (int t = 0; t < 4; t++) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
        if (t == 1) d.textureType = MTLTextureTypeCube;
        d.storageMode = MTLStorageModeShared; textures[t] = [mtl newTextureWithDescriptor:d];
        if (t == 1) for (int f = 0; f < 6; f++) [textures[t] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:f withBytes:values[t] bytesPerRow:16 bytesPerImage:16];
        else [textures[t] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:values[t] bytesPerRow:16];
    }
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new]; sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
    sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeRepeat;
    id<MTLSamplerState> sampler = [mtl newSamplerStateWithDescriptor:sd];
    id<MTLTexture> nm = car_normals_port_texture(mtl, family);
    if (!nm) { fprintf(stderr, "no map\n"); return 1; }
    float c[2][4] = {{0}}, m[52][4] = {{0}}; uint8_t aux[16 + 16 * 16] = {0};
    m[0][0] = 64; m[0][1] = .5; m[1][0] = m[1][1] = m[1][2] = 1; m[3][3] = 1; m[4][0] = m[4][1] = m[4][2] = .25;
    m[7][1] = 1; m[8][0] = m[8][1] = m[8][2] = .2; m[10][0] = 1; m[11][3] = -1;
    id<MTLCommandQueue> q = [mtl newCommandQueue]; id<MTLCommandBuffer> cb = [q commandBuffer];
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = target; rp.colorAttachments[0].loadAction = MTLLoadActionClear; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:rp]; [enc setRenderPipelineState:pso];
    [enc setFragmentBytes:c length:sizeof c atIndex:0]; [enc setFragmentBytes:aux length:sizeof aux atIndex:3]; [enc setFragmentBytes:m length:sizeof m atIndex:4];
    for (int t = 0; t < 4; t++) { [enc setFragmentTexture:textures[t] atIndex:t]; [enc setFragmentSamplerState:sampler atIndex:t]; }
    [enc setFragmentTexture:textures[3] atIndex:14]; [enc setFragmentTexture:nm atIndex:15];
    [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
    if (cb.status != MTLCommandBufferStatusCompleted) return 1;
    static uint8_t px[512 * 256 * 4];
    [target getBytes:px bytesPerRow:512 * 4 fromRegion:MTLRegionMake2D(0, 0, 512, 256) mipmapLevel:0];
    fwrite(px, 1, sizeof px, stdout);
    return 0;
}}
'''


def gpu(d, material):
    helper = material[material.index('#define CAR_PAINT_POW_MSL'):material.index('\n\nstatic const char car_paint_msl')]
    body = material[material.index('static const char car_paint_msl'):material.index('\n// BOOT_DIAG_CARPAINT_AB=1')]
    emit = d / 'emit.c'
    emit.write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include "../host/shaders/sm_coverage.h"\n' +
                    '#define CAR_SHADOW_OFF16 ""\n#define PCS_CAR_SHADOW_FLOOR "0.2"\n' +
                    lits.text(None, 'port car normals fixture') + '\n' + helper + '\n' + body +
                    '\nstatic int car_locallight_enabled(void){return 1;}\n' +
                    material[material.index('static const char car_local_math'):material.index('static char *car_local_shader')] +
                    '\n'.join(function(material, n) for n in ('dup_replace', 'edit', 'car_local_shader')) +
                    '\nint main(void){char *s=car_local_shader(strdup(car_paint_msl),0,0);if(!s)return 1;fputs(s,stdout);free(s);}\n')
    run(['clang', '-I', RT, emit, '-o', d / 'emit'])
    shader = d / 'paint.metal'
    shader.write_text(run([d / 'emit']).stdout)
    (d / 'fixture.m').write_text(HARNESS)
    run(['clang', '-O1', '-fobjc-arc', '-I', RT, d / 'fixture.m', RT / 'car_normals_port.m',
         '-framework', 'Foundation', '-framework', 'Metal', '-o', d / 'fixture'])
    changed = {}
    for i, fam in enumerate(FAMILIES[:3]):
        shots = []
        for flat in (0, 1, 1):
            env = dict(os.environ)
            env.pop('NFS_TEST_CARNM_FLAT', None)
            if flat:
                env['NFS_TEST_CARNM_FLAT'] = '1'
            p = subprocess.run([str(d / 'fixture'), str(shader), str(i)], capture_output=True, env=env)
            if p.returncode == 3:
                return None
            assert p.returncode == 0, p.stderr.decode()[-3000:]
            shots.append(np.frombuffer(p.stdout, np.uint8).reshape(-1, 4))
        changed[fam] = (int((shots[0] != shots[1]).any(1).sum()), int((shots[1] != shots[2]).any(1).sum()))
    return changed


def main():
    pc = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    if not (pc / 'GLOBAL/GLOBALB.BUN').exists() or not (pc / 'CARS/997S/GEOMETRY.BIN').exists():
        print('SKIP car_normals_port_test: no PC game folder')
        return 3
    material = (RT / 'materials.m').read_text()
    diff = gen.pc_diffuses(pc)
    with tempfile.TemporaryDirectory(prefix='car-normals-port-') as tmp:
        d = Path(tmp)
        produced = (RT / 'gen/port_car_normals.inc').read_text()
        assert produced == gen.table(*gen.build(pc)), 'gen/port_car_normals.inc is not this generator\'s output'
        maps, slope = parse(produced)
        ok, note = content(maps, slope, diff)
        routes = route(d, material)
        bound = {f: routes['port', 0][f][1] == i + 1 and routes['port', 0][f][0] == 0 for i, f in enumerate(FAMILIES[:3])}
        bound['DAMAGE0'] = routes['port', 0]['DAMAGE0'] == (0, 0)
        today = sum(routes['x360', 0][f][1] != 0 for f in FAMILIES)
        console = sum(routes[v, 1][f] == (0xC0DE0000 + i, 0) for v in ('x360', 'port') for i, f in enumerate(FAMILIES)) // 2
        pc_off = sum(routes['pc', c][f] == (0, 0) for c in (0, 1) for f in FAMILIES)
        new = {f: ok[f] and bound[f] for f in FAMILIES}
        for f in FAMILIES:
            print('%-8s binding %s, content %s (%s)' % (f, 'PASS' if bound[f] else 'FAIL', 'PASS' if ok[f] else 'FAIL', note[f]))
        print('route: port maps bound by today\'s x360 route %d/4; console sentinels kept %d/4; pc off %d/8' % (today, console, pc_off))
        assert all(new.values()), 'new: %d/4 families pass' % sum(new.values())
        assert today == 0 and console == 4 and pc_off == 8
        print('old route 0/4 families bind a port map; new %d/4 pass' % sum(new.values()))
        # Recipe deletion: each removed recipe fails its own family's check and only that one.
        for fam in FAMILIES:
            saved = dict(gen.RECIPES)
            del gen.RECIPES[fam]
            try:
                broken, bslope = parse(gen.table(*gen.build(pc)))
            finally:
                gen.RECIPES.clear(); gen.RECIPES.update(saved)
            bok, _ = content(broken, bslope, diff)
            failed = [f for f in FAMILIES if not bok[f]]
            assert failed == [fam], 'deleting %s failed %s' % (fam, failed)
        print('recipe deletion: 4/4 runs fail exactly their own family')
        changed = gpu(d, material)
        if changed is None:
            print('SKIP GPU part: no Metal device')
            return 3
        for fam, (port, control) in changed.items():
            print('GPU %-8s changed px port vs flat %d, flat vs flat %d' % (fam, port, control))
            assert port > 512 * 256 // 20 and control == 0
    print('car_normals_port_test PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
