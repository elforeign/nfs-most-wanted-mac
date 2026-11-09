"""Finite-source visibility against independent ray/triangle quadrature.

CPU helpers 66/66, maximum quadrature error .0037589%; visibility omission
fails 16 and leaks 8. Metal helpers 1536/1536, maximum error .002609068%,
0/125 forbidden pixels lit. Visibility omission fails 260 and leaks 56;
tenfold units and colour normalization each fail 481; stale frame/generation
fail 4 each, wall normal/work cap 2 each, epoch truncation 4. The pinned
original retail fragment runs successfully but fails 13/24 finite-strip checks.
These are numerical helpers, not yet a bound renderer/GPU proof.
"""
import ctypes as C
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import subprocess

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from underglow_render_test import Vec, Strip, library


class Triangle(C.Structure):
    _fields_ = [('v', Vec*3)]


class Snapshot(C.Structure):
    _fields_ = [('frame', C.c_uint64), ('generation', C.c_uint64), ('centre', Vec),
                ('radius', C.c_double), ('count', C.c_uint), ('complete', C.c_uint),
                ('grounded', C.c_uint), ('triangles', Triangle*64)]


def tri(a, b, c):
    return Triangle((Vec*3)(Vec(*a), Vec(*b), Vec(*c)))


def rectangle(z, x=(-3, 3), y=(-3, 3)):
    a, b, c, d = [(x[0], y[0], z), (x[1], y[0], z), (x[1], y[1], z), (x[0], y[1], z)]
    return [tri(a, b, c), tri(a, c, d)]


def reference(strip, receiver, normal, triangles, flux):
    # Midpoint quadrature plus Moller-Trumbore rays, independent of the
    # production projected-cone interval construction and antiderivative.
    a = np.array([strip.a.x, strip.a.y, strip.a.z])
    b = np.array([strip.b.x, strip.b.y, strip.b.z])
    en = np.array([strip.normal.x, strip.normal.y, strip.normal.z])
    count = 65536
    points = a + ((np.arange(count)+.5)/count)[:, None]*(b-a)
    ray = points-np.array(receiver)
    r2 = np.sum(ray*ray, axis=1)
    energy = np.maximum(0, np.sum(-ray*en, axis=1)) * np.maximum(0, np.sum(ray*np.array(normal), axis=1))/r2**2
    for triangle in triangles:
        v = np.array([[p.x, p.y, p.z] for p in triangle.v])
        e1, e2 = v[1]-v[0], v[2]-v[0]
        p = np.cross(ray, e2)
        det = np.sum(p*e1, axis=1)
        valid = abs(det) > 1e-12
        det = np.where(valid, det, 1)
        t = np.array(receiver)-v[0]
        u = np.sum(p*t, axis=1)/det
        q = np.cross(t, e1)
        w = np.sum(ray*q, axis=1)/det
        s = np.dot(e2, q)/det
        blocked = valid & (u >= 0) & (w >= 0) & (u+w <= 1) & (s > 1e-7) & (s < 1-1e-7)
        energy[blocked] = 0
    return float(energy.sum()*np.linalg.norm(b-a)*flux/(math.pi*count))


def bind(lib):
    lib.ug_visible_lux.argtypes = [C.c_double, C.POINTER(Strip), Vec, Vec, C.POINTER(Triangle), C.c_uint, C.POINTER(C.c_double)]
    lib.ug_receiver_admit.argtypes = [C.POINTER(Snapshot), C.c_uint64, C.c_uint64, Vec, Vec]


def cases():
    wall = [tri((0, -.3, 0), (0, .3, 0), (0, .3, .4)),
            tri((0, -.3, 0), (0, .3, .4), (0, -.3, .4))]
    for height in (.0194143, .04, .15, .45):
        source = Strip(Vec(-1, 0, height), Vec(1, 0, height), Vec(0, 0, -1), 2, .01)
        for p in ((0, 0, 0), (.9, .2, 0), (1.3, -.4, 0)):
            for slope in (0, .2):
                n = (0, math.sin(slope), math.cos(slope))
                yield source, p, n, [], 'unblocked'
        yield source, (.4, .15, 0), (0, 0, 1), wall, 'partial-wall'
        yield source, (0, 0, -3), (0, 0, 1), rectangle(0), 'lower-deck'
        yield source, (.2, .3, 0), (0, 0, 1), rectangle(height/2, (-.4, .4), (-.1, .3)), 'partial-body'
        yield source, (.2, .3, 0), (0, 0, 1), rectangle(height/2), 'full-body'
        yield source, (.2, .3, 0), (0, 0, 1), rectangle(height+.1), 'beyond-emitter'
    tilt = math.sqrt(4+.4**2)
    source = Strip(Vec(-1, 0, .1), Vec(1, 0, .5), Vec(.4/tilt, 0, -2/tilt), tilt, .01)
    yield source, (.8, .1, .15), (0, 0, 1), [], 'clipped-receiver-cosine'
    source = Strip(Vec(-1, 0, .15), Vec(1, 0, .15), Vec(.6, 0, -.8), 2, .01)
    yield source, (0, .2, 0), (0, 0, 1), [], 'clipped-emitter-cosine'
    source = Strip(Vec(-1, 0, .025), Vec(1, 0, .025), Vec(0, 0, -1), 2, .01)
    yield source, (1.01, .005, 0), (0, 0, 1), [], 'near-endpoint'


def measured_rgb_flux(preset):
    # Independent measured primaries and published XYZ/Rec.709 transform,
    # rather than using the production conversion to generate its own oracle.
    primaries = ((51.857, .694641, .304843), (106.075, .159396, .722814), (29.469, .133762, .060996))
    xyz = np.zeros(3)
    for channel, (flux, x, y) in enumerate(primaries):
        if preset & (1 << channel): xyz += [flux*x/y, flux, flux*(1-x-y)/y]
    raw = np.array([[3.2404542, -1.5371385, -.4985314], [-.9692660, 1.8760108, .0415560],
                    [.0556434, -.2040259, 1.0572252]])
    rgb = np.maximum(0, np.sum(raw*xyz, axis=1))
    luminance = float(np.sum(rgb*np.array([.2126729, .7151522, .0721750])))
    return rgb*(xyz[1]/luminance) if luminance else rgb


def measure(lib, data):
    bind(lib)
    checks = 0
    failures = []
    maxerror = 0
    leaks = 0
    for strip, p, normal, triangles, name, expected in data:
        t = (Triangle*len(triangles))(*triangles)
        output = C.c_double(-1)
        valid = lib.ug_visible_lux(106.075, C.byref(strip), Vec(*p), Vec(*normal), t, len(t), C.byref(output))
        checks += 1
        error = abs(output.value-expected)/max(1, expected)
        maxerror = max(maxerror, error)
        if not valid or error > .001:
            failures.append((name, output.value, expected))
        if expected == 0 and output.value != 0:
            leaks += 1
    # Admission requires actual collision triangle containment and a current,
    # complete, grounded snapshot. A wall is not a road receiver.
    s = Snapshot(frame=100, generation=7, centre=Vec(0, 0, 0), radius=3, count=2, complete=1, grounded=1)
    for i, triangle in enumerate(rectangle(0)):
        s.triangles[i] = triangle
    def admit(p=(.3, .2, 0), normal=(0, 0, 1), frame=100, generation=7):
        return lib.ug_receiver_admit(C.byref(s), frame, generation, Vec(*p), Vec(*normal))
    for want, result, label in [(1, admit(), 'ground'), (0, admit(frame=101), 'stale frame'),
                (0, admit(generation=8), 'stale geometry'), (0, admit(generation=0), 'missing generation'),
                (0, admit(frame=100+(1 << 32)), 'high frame bits'), (0, admit(generation=7+(1 << 32)), 'high generation bits'),
                (0, admit(p=(.3, .2, -3)), 'lower deck admission'), (0, admit(p=(.3, .2, .01)), 'missing ground'),
                (0, admit(p=(4, .2, 0)), 'bounded footprint'), (0, admit(normal=(1, 0, 0)), 'wall normal'),
                (0, admit(normal=(0, 0, 2)), 'nonunit normal')]:
        checks += 1
        if result != want: failures.append((label, result, want))
    for field, bad in [('complete', 0), ('grounded', 0), ('count', 65), ('count', 0), ('radius', 7)]:
        old = getattr(s, field)
        setattr(s, field, bad)
        checks += 1
        if admit(): failures.append(('invalid '+field, 1, 0))
        setattr(s, field, old)
    s.count = 3; s.triangles[2].v[0].x = float('nan')
    checks += 1
    if admit(): failures.append(('malformed tail after admitted floor', 1, 0))
    s.count = 2
    value = C.c_double(123)
    strip = data[0][0]
    oversized = (Triangle*65)(*([rectangle(0)[0]]*65))
    checks += 1
    if lib.ug_visible_lux(106, C.byref(strip), Vec(0, 0, 0), Vec(0, 0, 1), oversized, 65, C.byref(value)) or value.value:
        failures.append(('blocker work cap', value.value, 0))
    checks += 1
    if not lib.ug_visible_lux(0, C.byref(strip), Vec(0, 0, 0), Vec(0, 0, 1), None, 0, C.byref(value)) or value.value:
        failures.append(('off contribution', value.value, 0))
    return checks, failures, maxerror, leaks


GPU = r'''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc,char **argv) { @autoreleasepool {
 if(argc!=6)return 2;id<MTLDevice> d=MTLCreateSystemDefaultDevice();if(!d)return 77;
 NSError *error=nil;NSString *source=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error];
 if(!source){fprintf(stderr,"source: %s\n",error.description.UTF8String);return 2;}
 MTLCompileOptions *options=[MTLCompileOptions new];options.fastMathEnabled=NO;
 id<MTLLibrary> lib=[d newLibraryWithSource:source options:options error:&error];
 if(!lib){fprintf(stderr,"shader compile: %s\n",error.description.UTF8String);return 2;}
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
 pd.vertexFunction=[lib newFunctionWithName:@"ug_fixture_vertex"];pd.fragmentFunction=[lib newFunctionWithName:@"ug_fixture_fragment"];
 pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
 id<MTLRenderPipelineState> pso=[d newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!pso){fprintf(stderr,"pipeline: %s\n",error.description.UTF8String);return 2;}
 NSData *cases=[NSData dataWithContentsOfFile:@(argv[2])],*triangles=[NSData dataWithContentsOfFile:@(argv[3])];
 unsigned count=(unsigned)strtoul(argv[4],NULL,10);if(!count||!cases||!triangles)return 2;
 id<MTLBuffer> cbuff=[d newBufferWithBytes:cases.bytes length:cases.length options:MTLResourceStorageModeShared];
 id<MTLBuffer> tbuff=[d newBufferWithBytes:triangles.bytes length:triangles.length options:MTLResourceStorageModeShared];
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:count height:1 mipmapped:NO];
 td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
 id<MTLTexture> out=[d newTextureWithDescriptor:td];if(!out||!cbuff||!tbuff)return 2;
 MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
 rp.colorAttachments[0].texture=out;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 id<MTLCommandQueue> queue=[d newCommandQueue];id<MTLCommandBuffer> command=[queue commandBuffer];
 id<MTLRenderCommandEncoder> enc=[command renderCommandEncoderWithDescriptor:rp];[enc setRenderPipelineState:pso];
 [enc setFragmentBuffer:cbuff offset:0 atIndex:0];[enc setFragmentBuffer:tbuff offset:0 atIndex:1];
 [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[enc endEncoding];[command commit];[command waitUntilCompleted];
 if(command.status==MTLCommandBufferStatusError){fprintf(stderr,"execution: %s\n",command.error.description.UTF8String);return 2;}
 float *values=calloc((size_t)count*4,sizeof(float));[out getBytes:values bytesPerRow:(size_t)count*16 fromRegion:MTLRegionMake2D(0,0,count,1) mipmapLevel:0];
 FILE *f=fopen(argv[5],"wb");if(!f)return 2;size_t written=fwrite(values,16,count,f);int closed=fclose(f);free(values);return written==count&&!closed?0:2;
}}
'''

GPU_ENTRY = r'''
struct UGFixtureCase { float4 a,b,en,receiver,normal,flux,albedo; uint4 state; uint4 epoch,epoch_high; };
vertex float4 ug_fixture_vertex(uint id [[vertex_id]]) {
 float2 positions[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(positions[id],0,1);
}
fragment float4 ug_fixture_fragment(float4 position [[position]],constant UGFixtureCase *cases [[buffer(0)]],constant float4 *triangles [[buffer(1)]]) {
 uint index=uint(position.x);UGFixtureCase c=cases[index];
 if(!ug_receiver_current(c.normal.xyz,c.state.y,(ulong(c.epoch_high.x)<<32)|c.epoch.x,(ulong(c.epoch_high.y)<<32)|c.epoch.y,
     (ulong(c.epoch_high.z)<<32)|c.epoch.z,(ulong(c.epoch_high.w)<<32)|c.epoch.w)||!any(c.flux.xyz>0))return float4(0);
 float unit_lux=ug_visible(1.0f,c.a.xyz,c.b.xyz,c.en.xyz,c.receiver.xyz,c.normal.xyz,triangles+index*195,c.state.x);
 return float4(ug_diffuse(unit_lux,c.flux.xyz,c.albedo.xyz),unit_lux);
}
'''

ORIGINAL_VERTEX = r'''
vertex SmPSStageIn ug_fixture_vertex(uint i [[vertex_id]],constant float4 &eye [[buffer(0)]]) {
 SmPSStageIn r={};r.pos=float4(i==1?3.0f:-1.0f,i==2?3.0f:-1.0f,0,1);
 r.color0=float4(.25f,.25f,.25f,1);r.texcoord0=float4(.5f,.5f,0,1);r.texcoord1=float4(.5f,.5f,0,1);
 r.texcoord3=float4(0,0,1,0);r.texcoord4=eye;r.texcoord5=float4(0);r.texcoord6=float4(1);return r;
}
'''


def original_gpu_control(tmp, lib):
    # Execute the actual, unedited retail worldreflect fragment entry into
    # RGBA32Float, with eight finite-strip settings bound separately. It has no
    # emitter contribution; the measured nonzero reference must reject it.
    fixture = ROOT/'host/shaders/testdata/worldreflect/t0_world_reflect_noshadow_2_0_p0_ps_2_0.bin'
    if not fixture.is_file():
        print('SKIP: pinned original worldreflect program required')
        return None
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == 'e06eb3f3473d65bfdbe95ca3810484d04da00c80f5774c8978a1d40a2daf37ca'
    from roadfilm_test import TRANSLATOR
    translator = tmp/'original_translate.cpp'; translator.write_text(TRANSLATOR)
    executable = tmp/'original_translate'
    subprocess.run(['clang++', '-std=c++17', '-O1', '-I', str(ROOT/'host/shaders'), str(translator),
                    str(ROOT/'host/shaders/sm_parse.cpp'), str(ROOT/'host/shaders/sm_msl.cpp'), '-o', str(executable)], check=True, capture_output=True)
    shader = tmp/'original.metal'
    subprocess.run([str(executable), str(fixture), str(shader)], check=True, capture_output=True)
    shader.write_text(shader.read_text()+ORIGINAL_VERTEX)
    driver = GPU.replace('#include <stdio.h>', '#include <stdio.h>\n#include "sm_translate.h"')
    driver = driver.replace('newFunctionWithName:@"ug_fixture_fragment"', 'newFunctionWithName:@"sm_main"')
    textures = r'''
 id<MTLTexture> tex[4];MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;
 id<MTLSamplerState> sampler=[d newSamplerStateWithDescriptor:sd];
 MTLTextureDescriptor *tt=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
 tt.storageMode=MTLStorageModeShared;tt.usage=MTLTextureUsageShaderRead;
 const float pixels[4][4]={{.4f,.2f,.1f,.6f},{.5f,.5f,1,1},{.5f,.5f,.5f,1},{.3f,.5f,.7f,1}};
 for(int i=0;i<4;++i){tex[i]=[d newTextureWithDescriptor:tt];[tex[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:pixels[i] bytesPerRow:16];}
'''
    driver = driver.replace(' MTLRenderPipelineDescriptor *pd=', textures+'\n MTLRenderPipelineDescriptor *pd=', 1)
    binding = '[enc setFragmentBuffer:cbuff offset:0 atIndex:0];[enc setFragmentBuffer:tbuff offset:0 atIndex:1];'
    assert driver.count(binding) == 1
    driver = driver.replace(binding, r'''
 [enc setFragmentBuffer:cbuff offset:0 atIndex:0];[enc setFragmentBuffer:tbuff offset:0 atIndex:5];
 int integers[16][4]={{0}},booleans[16]={0};SmPSAux aux={0};float eye[4]={0,0,1,0};
 [enc setVertexBytes:eye length:sizeof eye atIndex:0];[enc setFragmentBytes:integers length:sizeof integers atIndex:1];
 [enc setFragmentBytes:booleans length:sizeof booleans atIndex:2];[enc setFragmentBytes:&aux length:sizeof aux atIndex:3];
 for(int i=0;i<4;++i){[enc setFragmentTexture:tex[i] atIndex:i];[enc setFragmentSamplerState:sampler atIndex:i];}
''')
    file = tmp/'original_gpu.m'; file.write_text(driver)
    gpu = tmp/'original_gpu'
    subprocess.run(['clang', '-O2', '-fobjc-arc', '-I', str(ROOT/'host/shaders'), '-framework', 'Foundation',
                    '-framework', 'Metal', str(file), '-o', str(gpu)], check=True, capture_output=True)
    constants = np.zeros((256, 4), dtype='<f4'); constants[2, 0] = 1
    constants_file = tmp/'original_constants.bin'; constants_file.write_bytes(constants.tobytes())
    fluxes = []
    for preset in range(8):
        rgb, xyz_values, flux = (C.c_double*3)(), (C.c_double*3)(), (C.c_double*3)(); error = C.c_double()
        assert lib.ug_preset_rgb(preset, rgb) and lib.ug_mix_xyz(rgb, xyz_values) and lib.ug_xyz_rec709(xyz_values, flux, C.byref(error))
        fluxes.append([*flux, 0])
    source_file = tmp/'original_strip_settings.bin'; source_file.write_bytes(np.array(fluxes, dtype='<f4').tobytes())
    output = tmp/'original.f32'
    result = subprocess.run([str(gpu), str(shader), str(constants_file), str(source_file), '8', str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    values = np.frombuffer(output.read_bytes(), dtype='<f4').reshape(8, 4)
    assert np.isfinite(values).all() and np.max(abs(values[0]-[.2, .1, .05, 1])) < 1e-5, values.tolist()
    delta = values[:, :3]-values[0, :3]
    albedo = np.array([.4, .2, .1]); albedo = ((albedo+.055)/1.055)**2.4
    finite_unit = 353.090215088/106.075
    reference_rgb = finite_unit*np.array(fluxes)[:, :3]*albedo/(math.pi*100)
    failed = abs(delta-reference_rgb) > .05*np.maximum(reference_rgb, 1e-6)
    assert np.count_nonzero(failed) > 0 and np.count_nonzero(delta) == 0, 'original zero-contribution path escaped'
    print(f'Pinned original retail sm_main: {np.count_nonzero(failed)}/{failed.size} finite-strip checks rejected; measured added RGB exactly zero')
    return dict(shader_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
                checks=int(failed.size), failures=int(np.count_nonzero(failed)), added_nonzero_channels=int(np.count_nonzero(delta)))


def gpu_check(tmp, lib, data):
    lib.ug_render_msl.restype = C.c_char_p
    math_source = lib.ug_render_msl().decode()
    lib.ug_preset_rgb.argtypes = [C.c_uint, C.POINTER(C.c_double)]
    lib.ug_mix_xyz.argtypes = [C.POINTER(C.c_double), C.POINTER(C.c_double)]
    lib.ug_xyz_rec709.argtypes = [C.POINTER(C.c_double), C.POINTER(C.c_double), C.POINTER(C.c_double)]
    packed, triangles, expected, labels = [], [], [], []
    for strip, p, normal, blockers, name, reference_lux in data:
        for preset in range(8):
            rgb, xyz_values, flux = (C.c_double*3)(), (C.c_double*3)(), (C.c_double*3)()
            error = C.c_double()
            assert lib.ug_preset_rgb(preset, rgb) and lib.ug_mix_xyz(rgb, xyz_values) and lib.ug_xyz_rec709(xyz_values, flux, C.byref(error))
            assert abs(error.value) < 1e-12
            wanted_flux = measured_rgb_flux(preset)
            assert np.max(abs(np.array(flux)-wanted_flux)) < 1e-10
            albedo = [.04+.02*preset, .12, .2-.01*preset]
            vectors = [[strip.a.x, strip.a.y, strip.a.z, 0], [strip.b.x, strip.b.y, strip.b.z, 0],
                       [strip.normal.x, strip.normal.y, strip.normal.z, 0], [*p, 0], [*normal, 0], [*flux, 0], [*albedo, 0]]
            raw = np.array(vectors, dtype='<f4').tobytes() + np.array([len(blockers), 1, 0, 0, 100, 100, 7, 7, 0, 0, 0, 0], dtype='<u4').tobytes()
            packed.append(raw)
            # One extra accessible canary face lets the over-budget mutation
            # fail numerically without reading outside its test allocation.
            t = np.zeros((65, 3, 4), dtype='<f4')
            for i, triangle in enumerate(blockers):
                t[i, :, :3] = [[v.x, v.y, v.z] for v in triangle.v]
            triangles.append(t.tobytes())
            unit = reference_lux/106.075 if preset else 0
            expected.append([*(unit*wanted_flux*albedo/(math.pi*100)), unit])
            labels.append((name, preset))
    # Epoch/ground/material admission are checked before the finite-source math.
    for label in ('stale-frame', 'stale-generation', 'missing-ground', 'wall', 'work-cap', 'airborne', 'high-frame', 'high-generation'):
        raw = bytearray(packed[2])
        f = np.frombuffer(raw, dtype='<f4')
        u = np.frombuffer(raw, dtype='<u4')
        if label == 'stale-frame': u[32] = 99
        if label == 'stale-generation': u[34] = 6
        if label in ('missing-ground', 'airborne'): u[29] = 0
        if label == 'wall': f[16:19] = [1, 0, 0]
        if label == 'work-cap': u[28] = 65
        if label == 'high-frame': u[36] = 1
        if label == 'high-generation': u[38] = 1
        canary = triangles[2]
        if label == 'work-cap':
            extra = np.zeros((65, 3, 4), dtype='<f4')
            for i in range(65): extra[i, :, :3] = [[v.x, v.y, v.z] for v in rectangle(0)[0].v]
            canary = extra.tobytes()
        packed.append(bytes(raw)); triangles.append(canary); expected.append([0, 0, 0, 0]); labels.append((label, 2))
    cases_path, tri_path = tmp/'cases.bin', tmp/'triangles.bin'
    cases_path.write_bytes(b''.join(packed)); tri_path.write_bytes(b''.join(triangles))
    harness = tmp/'gpu.m'; harness.write_text(GPU)
    executable = tmp/'gpu'
    subprocess.run(['clang', '-O2', '-fobjc-arc', '-framework', 'Foundation', '-framework', 'Metal',
                    str(harness), '-o', str(executable)], check=True, capture_output=True)
    expected = np.array(expected)

    def render(source, name):
        path = tmp/(name+'.metal'); path.write_text(source+GPU_ENTRY)
        output = tmp/(name+'.f32')
        result = subprocess.run([str(executable), str(path), str(cases_path), str(tri_path), str(len(packed)), str(output)], capture_output=True, text=True)
        if result.returncode == 77:
            return None
        assert result.returncode == 0, (name, result.stderr[-2500:])
        return np.frombuffer(output.read_bytes(), dtype='<f4').reshape(-1, 4)

    values = render(math_source, 'production-math')
    if values is None:
        print('SKIP: numerical Metal check requires the test tool GPU sandbox')
        return 3
    relative = abs(values-expected)/np.maximum(abs(expected), 1e-6)
    bad = ~np.isfinite(values) | (relative > .05)
    forbidden = np.all(expected == 0, axis=1)
    leaks = np.any(values[forbidden] != 0, axis=1).sum()
    print(f'GPU finite-source helpers: {values.size-np.count_nonzero(bad)}/{values.size}; max error {100*relative.max():.6f}%; forbidden lit pixels {leaks}/{forbidden.sum()}')
    assert not bad.any() and leaks == 0, [(labels[i], values[i].tolist(), expected[i].tolist()) for i in np.where(bad.any(axis=1))[0]][:5]
    original_call = 'int status=ug_interval(r,a,b,tri[3*i].xyz,tri[3*i+1].xyz,tri[3*i+2].xyz,iv);'
    assert math_source.count(original_call) == 1
    controls = {
        'visibility-removed': math_source.replace(original_call, 'int status=0;'),
        'tenfold-unit-error': math_source.replace('M_PI_F*100.0f', 'M_PI_F*10.0f'),
        'colour-normalization': math_source.replace('max(float3(0),flux_rgb)', 'max(float3(0),flux_rgb)/max(max(flux_rgb.x,flux_rgb.y),flux_rgb.z)'),
        'stale-frame': math_source.replace('frame==expected_frame', 'true'),
        'stale-generation': math_source.replace('generation==expected_generation', 'true'),
        'wall-normal': math_source.replace('normal.z>=.5f', 'true'),
        'work-cap': math_source.replace('if(count>64||', 'if(false||'),
        'epoch-truncation': math_source.replace('frame==expected_frame', 'uint(frame)==uint(expected_frame)').replace('generation==expected_generation', 'uint(generation)==uint(expected_generation)'),
    }
    proof = dict(scope='numerical helpers; renderer binding not established',
                 source_sha256=hashlib.sha256(math_source.encode()).hexdigest(),
                 checks=int(values.size), failures=int(np.count_nonzero(bad)),
                 max_relative_error=float(relative.max()), forbidden_pixels=int(forbidden.sum()),
                 lit_forbidden_pixels=int(leaks), mutants={})
    for name, changed in controls.items():
        assert changed != math_source
        v = render(changed, name)
        failed = ~np.isfinite(v) | (abs(v-expected) > .05*np.maximum(abs(expected), 1e-6))
        leaked = int(np.any(v[forbidden] != 0, axis=1).sum())
        print(f'GPU mutant {name}: {np.count_nonzero(failed)}/{failed.size} rejected; forbidden lit pixels {leaked}')
        assert failed.any(), name
        proof['mutants'][name] = dict(failures=int(np.count_nonzero(failed)), checks=int(failed.size), lit_forbidden_pixels=leaked)
    proof['pinned_original'] = original_gpu_control(tmp, lib)
    if proof['pinned_original'] is None:
        return 3
    artifact = ROOT/'.metal-cache/underglow/visibility_proof.json'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(proof, indent=2)+'\n')
    return 0


def main():
    source = (ROOT / 'runtime/underglow_render.m').read_text()
    data = [(s, p, n, t, name, reference(s, p, n, t, 106.075)) for s, p, n, t, name in cases()]
    with tempfile.TemporaryDirectory(prefix='ug-visibility-') as directory:
        tmp = Path(directory)
        lib = library(tmp, source, 'visibility-good')
        checks, failures, error, leaks = measure(lib, data)
        print(f'visibility helpers: {checks-len(failures)}/{checks}; max quadrature error {100*error:.7f}%; forbidden leaks {leaks}')
        assert not failures, failures[:8]
        controls = {
            'visibility-removed': ('int status = ug_blocked_interval(receiver, strip->a, strip->b, &triangles[i], interval);', 'int status = 0;'),
            'stale-frame': ('snapshot->frame != frame', '0'),
            'stale-geometry': ('snapshot->generation != generation', '0'),
            'airborne': ('snapshot->grounded != 1', '0'),
            'missing-ground': ('fabs(ug_dot(n, p)) > .002', '0'),
            'work-cap': ('!strip || count > UG_MAX_BLOCKERS || (count && !triangles)', '!strip || (count && !triangles)'),
            'malformed-tail': ('admitted = 1;', 'return 1;'),
        }
        for name, (before, after) in controls.items():
            assert source.count(before) == 1
            changed = source.replace(before, after)
            count, failed, _, leak = measure(library(tmp, changed, name), data)
            print(f'visibility mutant {name}: {len(failed)}/{count} rejected; forbidden leaks {leak}')
            assert failed, name
        if '--cpu-only' not in sys.argv:
            return gpu_check(tmp, lib, data)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
