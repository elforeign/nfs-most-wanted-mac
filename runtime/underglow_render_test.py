"""Measured stock mount fixture; renders are not established by this check.

Production mount geometry is compared with independently decoded body triangles,
not a second table of chosen strip coordinates. Missing stock assets exit 3.
Mounts 3948/3948 on BODY_A and 3948/3948 on road BODY_B; wheel intrusion
fails 79, above-body 378, excess width 132,
lost chassis height 4, unsupported admission 5, missing normal normalization 4,
forward normal transform 2. Runtime observation context 15/15; reflected pose
fails 3, BASE-as-BODY 5, nested contamination 2, Off work 4, bounds/nearby/stale
receiver controls 1 each. These are helpers, not production light draws.
"""
import ctypes as C
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[1]
import numpy as np


CONTEXT = r'''
#include "host.h"
#include "underglow_render.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
int diag_on;
const char *diag_dir;
static unsigned cosmetic_queries, checks, failures;
uint64_t host_ticks(void) { return 0; }
unsigned underglow_get(uint32_t profile,uint32_t handle) { ++cosmetic_queries;return profile==0x81000&&handle==82?2:0; }
void diag_event(const char *fmt,...) {}
int roadfilm_shader_hash(uint64_t hash) { return hash==3; }
int postfx_look_variant(const char *variant) { return 0; }
#define CHECK(x) do { ++checks;if(!(x)){++failures;fprintf(stderr,"context line %d: %s\n",__LINE__,#x);} } while(0)
int main(int argc,char **argv) {
 if(argc!=2)return 2;diag_dir=argv[1];G=calloc(1,16*1024*1024);if(!G)return 2;
 const uint32_t self=0x50000,view=0x20000,matrix=0x52000,position=0x52100;
 M32(self+0x84)=0x51000;M32(0x51000)=41;M32(0x9B09D8)=0x70000;
 strcpy((char *)G+0x70000+41*0xd0,"bmwm3gtre46");M32(view+4)=1;
 M32(self+0xb84)=0x60000;M32(0x60008)=0x7D3E007B;
 M32(self+0xb84+0x17*0x14)=0x60100;M32(0x60108)=0x8E3052B5;
 M32(0x91CF90)=0x80000;M32(0x80010)=0x81000;M32(0x810A8)=82;
 for(int i=0;i<4;i++)MF32(matrix+20*i)=1;
 MF32(position+8)=3;
 Cpu c={0};c.ecx=self;c.esp=0x100000;c.eax=0x12345678;c.cw=0x37f;
 M32(c.esp+4)=view;M32(c.esp+8)=position;M32(c.esp+12)=matrix;
 UGCarCapture off;Cpu saved=c;
 CHECK(underglow_render_scene_step("garage",0)==-1);
 underglow_render_car_begin(&c,&off);underglow_render_car_end(&c,&off);
 CHECK(!off.valid&&cosmetic_queries==0&&!memcmp(&c,&saved,sizeof c));
 diag_on=1;CHECK(underglow_render_scene_step("bridge",0)==-1);
 CHECK(underglow_render_scene_step("garage",0)==0);
 M32(c.esp+4+8*4)=1;UGCarCapture reflected;
 underglow_render_car_begin(&c,&reflected);CHECK(!reflected.valid&&cosmetic_queries==0);
 underglow_render_car_end(&c,&reflected);
 M32(c.esp+4+8*4)=0;UGCarCapture outer; saved=c;
 underglow_render_car_begin(&c,&outer);
 CHECK(outer.valid&&outer.body==0x60100&&outer.preset==2&&cosmetic_queries==1);
 CHECK(!memcmp(&c,&saved,sizeof c));
 Cpu part=c;M32(part.esp+8)=self+0x10ac;M32(part.esp+12)=matrix;MF32(matrix+48)=99;
 Cpu child=c;child.ecx=0x90000;UGCarCapture nested;
 underglow_render_car_begin(&child,&nested);underglow_render_part(&part);underglow_render_car_end(&child,&nested);
 CHECK(!nested.valid&&outer.front_tyres==0);
 for(unsigned i=0;i<4;++i){M32(part.esp+8)=self+(i<2?0x10ac:0x10c0);MF32(matrix+48)=10+i;underglow_render_part(&part);}
 CHECK(outer.front_tyres==2&&outer.rear_tyres==2);
 CHECK(outer.tyre_world[0][12]==10&&outer.tyre_world[3][12]==13);
 c.eax=1;saved=c;underglow_render_car_end(&c,&outer);CHECK(!memcmp(&c,&saved,sizeof c));
 CHECK(underglow_render_scene_step("garage",1)==0);
 const uint32_t queue=0x66000,world=0x68000;
 M32(0x982A20)=view;M32(queue)=0x67000;M32(queue+16)=0x65000;M32(0x65004)=3;M32(queue+28)=world;
 for(int i=0;i<4;++i)MF32(world+20*i)=1;MF32(world+48)=1;MF32(world+52)=2;MF32(world+56)=3;
 float vertices[3][3]={{-2,-2,0},{2,-2,0},{2,2,0}};uint16_t indices[3]={0,1,2};
 UGMeshView mesh={.vertices=(void *)vertices,.indices=(void *)indices,.vertex_bytes=sizeof vertices,.index_bytes=sizeof indices,
  .stride=12,.index_width=2,.primitives=1,.vertex_generation=1,.index_generation=2,.shader=3};
 underglow_render_receiver_record(queue);underglow_render_mesh_observe(&mesh);underglow_render_mesh_observe(&mesh);
 mesh.vertex_generation=3;mesh.vertex_bytes=24;underglow_render_mesh_observe(&mesh);mesh.vertex_bytes=sizeof vertices;mesh.vertex_generation=1;
 mesh.base_vertex=-1;underglow_render_mesh_observe(&mesh);mesh.base_vertex=0;
 mesh.vertex_generation=4;for(unsigned i=0;i<3;++i)vertices[i][0]+=20;underglow_render_mesh_observe(&mesh);
 for(unsigned i=0;i<3;++i)vertices[i][0]-=20;mesh.vertex_generation=1;
 underglow_render_receiver_clear();mesh.vertex_generation=3;underglow_render_mesh_observe(&mesh);
 CHECK(underglow_render_scene_step("garage",60)==-1);
 char path[4096],line[4096];snprintf(path,sizeof path,"%s/underglow_meshes.jsonl",diag_dir);FILE *file=fopen(path,"r");
 unsigned rows=0;if(file){while(fgets(line,sizeof line,file))++rows;fclose(file);}CHECK(rows==1);
 CHECK(cosmetic_queries==1);
 printf("observation context: %u/%u, failures %u\n",checks-failures,checks,failures);
 free(G);return failures?1:0;
}
'''


def context_check(tmp, source):
    harness = tmp/'context.c'
    harness.write_text(CONTEXT)
    mutants = {'good': source,
               'reflected-pose': source.replace('if (ug_arg(c, 8)) return;', ''),
               'base-as-body': source.replace('+0x17*0x14+lod*4', '+0*0x14+lod*4'),
               'nested-contamination': source.replace('s->previous = active_capture; active_capture = NULL;', 's->previous = active_capture;'),
               'off-work': source.replace('if (!diag_on || !scene_requested) return;', ''),
               'mesh-bounds': source.replace('if (offset > m->vertex_bytes || m->vertex_bytes-offset < 12) return;', ''),
               'nearby-bound': source.replace('if (dx*dx+dy*dy > 12.25 || box[2] > scene_centre[2]+.5 || box[5] < scene_centre[2]-6) continue;', ''),
               'stale-receiver': source.replace('void underglow_render_receiver_clear(void) { receiver_observation.valid = 0; }', 'void underglow_render_receiver_clear(void) {}')}
    for name, text in mutants.items():
        assert name == 'good' or text != source
        unit = tmp/(name+'.c'); unit.write_text(text)
        executable = tmp/('context-'+name)
        result = subprocess.run(['clang', '-std=c11', '-O1', '-I', str(ROOT/'runtime'), '-I', str(ROOT/'recomp'),
                                 str(harness), str(unit), str(ROOT/'runtime/underglow_photometry.c'), '-o', str(executable)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:2300]
        result = subprocess.run([str(executable), str(tmp)], capture_output=True, text=True)
        print(name+': '+result.stdout.strip())
        assert result.returncode == (0 if name == 'good' else 1), result.stderr[-1500:]
        if name == 'good':
            observation = json.loads((tmp/'underglow_garage_observation.json').read_text())
            assert observation['status'] == 'observation-only' and observation['body_hash'] == 0x8E3052B5
            assert observation['front_tyres'] == observation['rear_tyres'] == 2
            rows = [json.loads(line) for line in (tmp/'underglow_meshes.jsonl').read_text().splitlines()]
            assert rows[0]['status'] == 'observation-only'
            assert len(rows) == 1 and rows[0]['complete'] and rows[0]['bounds'] == [-1, 0, 3, 3, 4, 3]
            assert rows[0]['vertex_generation'] == 1 and rows[0]['index_generation'] == 2
            assert (tmp/'underglow_mesh_000.bin').stat().st_size == 36


class Vec(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double), ('z', C.c_double)]


class Strip(C.Structure):
    _fields_ = [('a', Vec), ('b', Vec), ('normal', Vec), ('length', C.c_double), ('width', C.c_double)]


def library(tmp, text, label):
    source = tmp / (label + '.c')
    source.write_text(text)
    output = tmp / (label + '.dylib')
    subprocess.run(['clang', '-std=c11', '-O2', '-shared', '-fPIC', '-DUG_RENDER_CORE_ONLY=1',
                    '-I', str(ROOT / 'runtime'), str(source), str(ROOT / 'runtime/underglow_photometry.c'),
                    '-o', str(output)], check=True, capture_output=True)
    lib = C.CDLL(str(output))
    lib.ug_mount_fixture.argtypes = [C.c_char_p]
    lib.ug_mount_fixture.restype = C.POINTER(Strip)
    lib.ug_mount_transform.argtypes = [C.c_char_p, C.POINTER(C.c_float), C.POINTER(C.c_float), C.POINTER(Strip)]
    return lib


def xyz(v):
    return np.array([v.x, v.y, v.z])


def underside(triangles, x, y):
    a, b, c = triangles[:, 0, :2], triangles[:, 1, :2], triangles[:, 2, :2]
    d, e, p = b-a, c-a, np.array([x, y])-a
    den = d[:, 0]*e[:, 1] - e[:, 0]*d[:, 1]
    ok = abs(den) > 1e-9
    den = np.where(ok, den, 1)
    u = (p[:, 0]*e[:, 1] - p[:, 1]*e[:, 0])/den
    v = (d[:, 0]*p[:, 1] - d[:, 1]*p[:, 0])/den
    ok &= (u >= -1e-7) & (v >= -1e-7) & (u+v <= 1+1e-7)
    z = triangles[:, 0, 2] + u*(triangles[:, 1, 2]-triangles[:, 0, 2]) + v*(triangles[:, 2, 2]-triangles[:, 0, 2])
    return float(min(z[ok], default=float('nan')))


def scene_check(cc, game, observation_path, record, strips):
    """Compare live CPU mesh copies with an independently decoded stock floor.

    This proves the observed garage geometry, not a light draw or its material.
    Matching just an index count is deliberately insufficient.
    """
    path = game/'FRONTEND/PLATFORMS/CAREER_SAFEHOUSE.BIN'
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == '37d812765c651a26562340ec3bb3d9075a98667c9c15e5aed1093821230d499f'
    solid = None
    for top, start, size in cc.children(data):
        if top != 0x80134000:
            continue
        for cid, off, length in cc.walk(data, start+8, start+8+size):
            if cid != 0x80134010:
                continue
            chunks = list(cc.walk(data, off+8, off+8+length))

            def payload(kind, align=16):
                pos, count = next((pos, count) for key, pos, count in chunks if key == kind)
                # Native chunks are globally aligned, not relative to a slice.
                return data[(pos+8+align-1)&-align:pos+8+count]

            if payload(0x134011)[160:].split(b'\0')[0].decode('ascii') == 'BACKDROP_CRIB':
                solid = payload
                break
        if solid:
            break
    assert solid is not None
    material = solid(0x134b02)[:104]
    assert cc.u32(material, 48) == 0 and cc.u32(material, 64) == 222 and cc.u32(material, 68) == 0
    raw = np.frombuffer(solid(0x134b01, 128), np.uint8).reshape(-1, 36).copy()
    vertices = raw.view('<f4').reshape(-1, 9)[:, :3].astype(np.float64)
    indices = np.frombuffer(solid(0x134b03), '<u2')[:222*3].reshape(-1, 3)
    source = vertices[indices]
    folder = observation_path.parent
    candidates = [json.loads(row) for row in (folder/'underglow_meshes.jsonl').read_text().splitlines()]
    matches = []
    for candidate in candidates:
        if candidate['input_triangles'] != 222 or candidate['shader'] != '8abb0769bddd41b4':
            continue
        world = np.array(candidate['world']).reshape(4, 4)
        transformed = source@world[:3, :3]+world[3, :3]
        live = np.fromfile(folder/f'underglow_mesh_{candidate["sample"]:03d}.bin', '<f4').reshape(-1, 3, 3)
        assert len(live) == candidate['triangles'] == candidate['nearby_triangles'] and candidate['complete']
        errors = np.max(abs(live[:, None]-transformed[None, :]), axis=(2, 3))
        assert len(set(np.argmin(errors, axis=1))) == len(live) and errors.min(axis=1).max() < 1e-6
        vp = np.array(record['view_matrix']).reshape(4, 4)@np.array(record['projection']).reshape(4, 4)
        effective = np.array(candidate['world_view_projection_columns']).reshape(4, 4).T@np.linalg.inv(vp)
        assert candidate['wvp_valid'] and abs(effective-world).max() < 2e-6
        heights = []
        for strip in strips:
            a, b, normal = xyz(strip.a), xyz(strip.b), xyz(strip.normal)
            side = np.cross((b-a)/strip.length, normal)
            for t in np.linspace(0, 1, 65):
                for width in (-.5, 0, .5):
                    point = a+t*(b-a)+width*strip.width*side
                    height = point[2]-underside(live, point[0], point[1])
                    assert np.isfinite(height) and 0 < height < .2
                    heights.append(height)
        matches.append(candidate['sample'])
        print(f'observed garage floor: {len(live)}/{len(live)} source triangles match, max error {errors.min(axis=1).max():.9g} m; '
              f'{len(heights)}/{len(heights)} full-width mount samples above floor, height {min(heights):.9f}..{max(heights):.9f} m; material/render unvalidated')
    assert len(matches) == 1, 'missing or ambiguous live floor'


def measure(lib, triangles, wheels):
    failures = []
    checks = 0
    gaps = []

    def check(condition, label):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(label)

    check(not lib.ug_mount_fixture(None), 'null model')
    for name in (b'is300', b'bmwm3gtr', b'BMWM3GTRE46', b'bmwm3gtre46_KIT01', b''):
        check(not lib.ug_mount_fixture(name), 'unsupported model')
    mounts = lib.ug_mount_fixture(b'bmwm3gtre46')
    check(bool(mounts), 'stock model')
    if not mounts:
        return checks, failures, gaps
    for i in range(4):
        strip = mounts[i]
        a, b, normal = xyz(strip.a), xyz(strip.b), xyz(strip.normal)
        tangent = b-a
        length = np.linalg.norm(tangent)
        check(abs(length-strip.length) < 1e-8, 'measured length')
        check(abs(strip.width-.01) < 1e-12, '10 mm width')
        check(abs(np.dot(normal, normal)-1) < 1e-8 and normal[2] < -.99, 'downward unit normal')
        check(abs(np.dot(tangent, normal)) < 1e-8, 'optical normal perpendicular')
        side = np.cross(tangent/length, normal)
        # Full strip width, not just the centreline/endpoints.
        for t in np.linspace(0, 1, 65):
            for width in (-.5, 0, .5):
                p = a+t*tangent+width*strip.width*side
                gap = underside(triangles, p[0], p[1])-p[2]
                gaps.append(gap)
                check(np.isfinite(gap) and 0 < gap <= .010, 'mount attachment within 10 mm')
                for wheel in wheels:
                    # A conservative tyre box includes the actual mesh radius
                    # and its complete width, rather than a contact-point mask.
                    collision = abs(p[0]-wheel[0]) <= .344 and abs(p[1]-wheel[1]) <= .321
                    check(not collision, 'outside wheel envelope')
        if i < 2:
            check(a[0] > max(w[0] for w in wheels if w[0] < 0)+.344 and
                  b[0] < min(w[0] for w in wheels if w[0] > 0)-.344, 'between axles')
    # A pitched/translated chassis must carry its optical normal and endpoints.
    angle = .2
    basis = np.array([[np.cos(angle), 0, -np.sin(angle), 0], [0, 1, 0, 0],
                      [np.sin(angle), 0, np.cos(angle), 0], [99, 98, 97, 1]], dtype=np.float32)
    position = np.array([10, -20, 3], dtype=np.float32)
    output = (Strip*4)()
    call = lambda m: lib.ug_mount_transform(b'bmwm3gtre46', m.ctypes.data_as(C.POINTER(C.c_float)),
                    position.ctypes.data_as(C.POINTER(C.c_float)), output)
    check(call(basis), 'rigid pitched chassis')
    for i in range(4):
        check(np.max(abs(xyz(output[i].a) - (xyz(mounts[i].a) @ basis[:3, :3] + position))) < 1e-6, 'chassis position')
        check(np.max(abs(xyz(output[i].normal) - xyz(mounts[i].normal) @ basis[:3, :3])) < 1e-6, 'chassis normal')
        check(abs(np.dot(xyz(output[i].normal), xyz(output[i].normal))-1) < 1e-12, 'optical normal renormalized')
    for bad in ('scale', 'shear', 'reflection', 'nan', 'projection'):
        m = np.eye(4, dtype=np.float32)
        if bad == 'scale': m[0, 0] = 2
        if bad == 'shear': m[0, 1] = .1
        if bad == 'reflection': m[0, 0] = -1
        if bad == 'nan': m[0, 0] = float('nan')
        if bad == 'projection': m[0, 3] = .01
        check(not call(m) and all(x.length == 0 for x in output), 'reject '+bad)
    quantized = np.eye(4, dtype=np.float32); quantized[2, 2] = 1.00000715
    check(call(quantized), 'stock matrix quantization')
    for strip in output:
        check(abs(np.dot(xyz(strip.normal), xyz(strip.b)-xyz(strip.a))) < 1e-12, 'inverse-transpose optical normal')
    return checks, failures, gaps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--game', type=Path, default=ROOT.parent/'Need For Speed Most Wanted Black Edition')
    parser.add_argument('--observation', type=Path)
    args = parser.parse_args()
    game = args.game
    path = game / 'CARS/BMWM3GTRE46/GEOMETRY.BIN'
    if not path.is_file() or not (game / 'GLOBAL/attributes.bin').is_file():
        print('SKIP: stock car geometry and attributes required (--game)')
        return 3
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    try:
        import carbon_car as cc
        from wheel_attrib import Wheels
    except ModuleNotFoundError:
        print('SKIP: optional stock-geometry decoding tools required')
        return 3
    solids = cc.solids(path.read_bytes())
    solid = next(s for _, s in solids if cc.payload(s, 0x134011, 16)[160:].split(b'\0')[0].decode('ascii') == 'BMWM3GTRE46_KIT00_BODY_A')
    assert hashlib.sha256(solid).hexdigest() == '539e956eb7176d6572a4503ee6dc3730dd9223f0840b9af3ff84a43da4318bcc', 'stock body fixture changed'
    mesh = cc.native_wheel_model(solid)
    vertices = mesh['vertices'].view('<f4').reshape(-1, 9)[:, :3]
    triangles = vertices[mesh['indices'].reshape(-1, 3)].astype(np.float64)
    lod_solid = next(s for _, s in solids if cc.payload(s, 0x134011, 16)[160:].split(b'\0')[0].decode('ascii') == 'BMWM3GTRE46_KIT00_BODY_B')
    assert hashlib.sha256(lod_solid).hexdigest() == '1c5474906c926a5200f3c4fa8fdfc96111c6f8bd704d171e7d643c3708938efc', 'stock road LOD body changed'
    lod_mesh = cc.native_wheel_model(lod_solid)
    lod_vertices = lod_mesh['vertices'].view('<f4').reshape(-1, 9)[:, :3]
    lod_triangles = lod_vertices[lod_mesh['indices'].reshape(-1, 3)].astype(np.float64)
    attrs = Wheels(game / 'GLOBAL/attributes.bin', 'mw').extract('bmwm3gtre46')
    wheels = np.array(attrs['TireOffsets']).reshape(4, 4)
    assert np.allclose(wheels[:, 3], .33) and np.allclose(attrs['FECompressions'], .27)
    source = (ROOT / 'runtime' / 'underglow_render.m').read_text()
    with tempfile.TemporaryDirectory(prefix='ug-mount-') as directory:
        tmp = Path(directory)
        context_check(tmp, source)
        lib = library(tmp, source, 'good')
        result = measure(lib, triangles, wheels)
        checks, failures, gaps = result
        print(f'mounts: {checks-len(failures)}/{checks}; {len(vertices)} body vertices, {len(triangles)} triangles; attachment gap {min(gaps)*1000:.6f}..{max(gaps)*1000:.6f} mm')
        assert not failures, failures[:8]
        lod_checks, lod_failures, lod_gaps = measure(lib, lod_triangles, wheels)
        print(f'road LOD mounts: {lod_checks-len(lod_failures)}/{lod_checks}; attachment gap {min(lod_gaps)*1000:.6f}..{max(lod_gaps)*1000:.6f} mm')
        assert not lod_failures, lod_failures[:8]
        if args.observation:
            record = json.loads(args.observation.read_text())
            assert record['model'] == 'bmwm3gtre46' and record['body_hash'] == 0x8E3052B5
            assert record['handle'] == 82 and record['preset'] == 2 and record['front_tyres'] == record['rear_tyres'] == 2
            basis = np.array(record['basis'], dtype=np.float32)
            position = np.array(record['position'], dtype=np.float32)
            out = (Strip*4)()
            assert lib.ug_mount_transform(b'bmwm3gtre46', basis.ctypes.data_as(C.POINTER(C.c_float)),
                    position.ctypes.data_as(C.POINTER(C.c_float)), out)
            assert all(abs(np.dot(xyz(s.normal), xyz(s.normal))-1) < 1e-12 for s in out)
            assert all(abs(np.dot(xyz(s.normal), xyz(s.b)-xyz(s.a))) < 1e-12 for s in out)
            print('observed physical chassis: 4/4 mounted strips; world endpoint Z ranges (m): '+
                  ', '.join(f'{min(s.a.z,s.b.z):.9f}..{max(s.a.z,s.b.z):.9f}' for s in out))
            scene_check(cc, game, args.observation, record, out)
        mutations = {
            'wheel': source.replace('{1.15, -.8, -.012890}', '{1.5, -.8, -.012890}'),
            'above-body': source.replace('.026910', '.126910'),
            'wide': source.replace('1.3, .01}', '1.3, .1}'),
            'road-snap': source.replace('p.z*basis[10] + (end < 2 ? position[2] : 0)', 'p.z*basis[10]'),
            'unsupported': source.replace('!strcmp(model, "bmwm3gtre46")', '1'),
            'normal-renormalization': source.replace('out[i].normal.x /= normal_length; out[i].normal.y /= normal_length; out[i].normal.z /= normal_length;', ''),
            'normal-forward-transform': source.replace('if (end == 2) q =', 'if (0) q ='),
        }
        for name, changed in mutations.items():
            assert changed != source
            count, failed, _ = measure(library(tmp, changed, name), triangles, wheels)
            print(f'mount mutant {name}: {len(failed)}/{count} rejected')
            assert failed, name
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
