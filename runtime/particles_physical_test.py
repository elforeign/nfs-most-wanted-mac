"""Production aerosol phase, mode, transform and lamp contracts.

Controls: missing-normalization and backward-phase mutants must fail; the old
renderer lacks the Physical selector/shader and fails the integration checks.
The companion soft_smoke_test compiles all six production Metal variants and
executes the actual draw selector and 90 depth-fade cases.
"""
import ctypes
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'


def run(args, **kw):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, **kw)
    assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-3000:]
    return result.stdout


def main():
    source = (RT / 'particles_physical.c').read_text()
    material = (RT / 'materials.m').read_text()
    render = (RT / 'render.m').read_text()
    settings = (RT / 'settings_port.c').read_text()
    assert 's = particles_physical_shader(s);' in material
    assert 'return particles_physical_enabled() && on;' in render
    assert 'particles_physical_constants(&mk' in render
    assert 'particles_physical_lamps(&lamps)' in render
    row = settings.split('static Setting s_particles =', 1)[1].split('};', 1)[0]
    assert '.label = "Particles"' in row and '.def = 1' in row
    assert '{"Retail", "Physical", "PC", "X360"}' in row
    assert '{"retail", "physical", "pc", "x360"}' in row
    assert 'particles_physical_lamp(c);' in (RT / 'mirror.c').read_text()
    # Decode the actual MSL string, then execute its scalar HG function as C.
    literal = source.split('static const char particles_phase_msl[] =', 1)[1].split(';\n\n', 1)[0]
    import ast
    msl = ''.join(ast.literal_eval(s) for s in re.findall(r'"(?:\\.|[^"\\])*"', literal))
    phase = re.search(r'inline float particle_hg\(.*?\n\}', msl, re.S)[0]
    phase = phase.replace('inline float', 'float')
    with tempfile.TemporaryDirectory(prefix='particles-physical-') as directory:
        tmp = Path(directory)
        prefix = '#include <math.h>\nstatic float clamp(float x,float a,float b){return fminf(b,fmaxf(a,x));}\n#define max fmaxf\n'
        for label, body, broken in (
            ('production', phase, False),
            ('normalization mutant', phase.replace('12.566370614359172f * ', ''), True),
            ('backward mutant', phase.replace('- 2.0f*g*', '+ 2.0f*g*'), True),
        ):
            path = tmp / (label.replace(' ', '_') + '.c')
            path.write_text(prefix + body)
            library = path.with_suffix('.dylib')
            run(['clang', '-O1', '-dynamiclib', path, '-o', library])
            hg = ctypes.CDLL(str(library)).particle_hg
            hg.argtypes = [ctypes.c_float, ctypes.c_float]
            hg.restype = ctypes.c_float
            failures = 0
            checks = 0
            for g in (0, .56, .682):
                for mu in (-1, -.5, 0, .5, 1):
                    expected = (1-g*g) / (4*math.pi*(1+g*g-2*g*mu)**1.5)
                    failures += abs(hg(mu, g)-expected) > max(1e-6, expected*1e-5)
                    checks += 1
                n = 4000
                integral = sum(hg(-1+(i+.5)*2/n, g) for i in range(n))*4*math.pi/n
                moment = sum((-1+(i+.5)*2/n)*hg(-1+(i+.5)*2/n, g) for i in range(n))*4*math.pi/n
                failures += abs(integral-1) > .0001
                failures += abs(moment-g) > .0001
                checks += 2
            assert (failures > 0) == broken, (label, failures)
            print(f'{label}: {failures}/{checks} phase failures')
        harness = r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "particles_physical.h"
uint8_t *G;
int diag_on;
uint64_t frame_serial=1;
int postfx_look_variant(const char *v){(void)v;return 0;}
void diag_event(const char *format,...){(void)format;}
void lighting_particle_scene(float *a,float *s,float *d){
  for(int i=0;i<3;++i){a[i]=.2f;s[i]=.5f;d[i]=i==2;}
}
int main(int argc,char **argv){
  if(argc>1){return particles_physical_enabled();}
  G=calloc(0xa00000,1);assert(G);
  uint32_t viewport[4]={0,0,100,50};
  MatConsts m={0};particles_physical_set(0);particles_physical_constants(&m,1,viewport);
  assert(m.k[3][3]==0);
  particles_physical_set(1);M32(0x009195E0+0x70)=0x2000;
  for(int i=0;i<4;++i)MF32(0x2080+20*i)=i+1;
  MF32(0x2080+48)=12;
  particles_physical_constants(&m,1,viewport);
  assert(m.k[3][3]==1 && m.k[3][0]==.02f && m.k[3][1]==-.04f);
  assert(m.k[11][0]==-1 && m.k[11][1]==1);
  assert(m.k[4][0]==1 && m.k[5][1]==.5f && fabsf(m.k[6][2]-1.f/3)<1e-6f);
  assert(m.k[7][0]==-3 && m.k[7][3]==.25f);
  assert(m.k[8][3]==.682f && m.k[9][3]==.93f && m.k[8][0]==.2f);
  Cpu cpu={0};cpu.esp=0x1000;
  M32(0x1004)=0x4000;M32(0x4004)=1;M32(0x1008)=0x5000;M32(0x100c)=0x6000;
  MF32(0x1010)=.5f;M32(0x5008)=0x9DB90133;
  for(int i=0;i<4;++i)MF32(0x6000+20*i)=1;
  for(int i=0;i<3;++i){MF32(0x5010+4*i)=i+1;MF32(0x6030+4*i)=10*(i+1);}
  MF32(0x5020)=1;
  ParticleLamps l;particles_physical_lamp(&cpu);particles_physical_lamps(&l);
  assert(l.k[0][0]==1 && l.k[1][0]==11 && l.k[1][1]==22 && l.k[1][2]==33);
  assert(l.k[2][0]==1 && l.k[1][3]==.5f);
  particles_physical_lamp(&cpu);particles_physical_lamps(&l);assert(l.k[0][0]==1);
  ++frame_serial;particles_physical_lamps(&l);assert(l.k[0][0]==0);
  M32(0x4004)=2;particles_physical_lamp(&cpu);particles_physical_lamps(&l);assert(l.k[0][0]==1);
  ++frame_serial;
  M32(0x4004)=3;particles_physical_lamp(&cpu);particles_physical_lamps(&l);assert(l.k[0][0]==0);
  M32(0x4004)=1;MF32(0x1010)=0;particles_physical_lamp(&cpu);particles_physical_lamps(&l);assert(l.k[0][0]==0);
  MF32(0x1010)=1;M32(0x1014)=1;particles_physical_lamp(&cpu);particles_physical_lamps(&l);assert(l.k[0][0]==0);
  M32(0x1014)=0;M32(0x5008)=123;particles_physical_lamp(&cpu);particles_physical_lamps(&l);assert(l.k[0][0]==0);
  free(G);puts("PASS production scene constants/inverse, lamp transform, deduplication, stale/mirror/off/reflection/unrelated exclusions");
}
'''
        harness = harness.replace('int main(int argc,char **argv){',
            '#undef assert\n#define assert(x) do {if(!(x)){fprintf(stderr,"contract line %d\\n",__LINE__);return 1;}}while(0)\nint main(int argc,char **argv){')
        path = tmp / 'contract.c'
        path.write_text('#include <stdio.h>\n' + harness)
        binary = tmp / 'contract'
        run(['clang', '-O1', '-DNFS_PUBLIC=1', '-I', RT, '-I', ROOT / 'recomp', path, RT / 'particles_physical.c', '-o', binary])
        print(run([binary]).strip())
        cases = 0
        for value, expected in ((None, 1), ('', 1), ('retail', 0), ('physical', 1), ('pc', 0), ('x360', 0), ('junk', 0)):
            env = {k:v for k,v in os.environ.items() if k!='NFS_PARTICLES'}
            if value is not None: env['NFS_PARTICLES']=value
            result = subprocess.run([str(binary), 'mode'], env=env, capture_output=True, text=True)
            assert result.returncode==expected and not result.stderr
            cases += 1
        print(f'PASS {cases} fresh-process selectors; default Physical')
        for label, broken in (
            ('stale lamp mutant', source.replace('lamp_frame==frame_serial &&', '1 &&')),
            ('transposed inverse mutant', source.replace('input[4*r+c]', 'input[4*c+r]')),
        ):
            assert broken != source
            mutant = tmp / 'mutant.c'
            mutant.write_text(broken)
            run(['clang', '-O1', '-DNFS_PUBLIC=1', '-I', RT, '-I', ROOT / 'recomp', path, mutant, '-o', binary])
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            assert result.returncode == 1, (label, result.returncode, result.stderr)
            print(f'CONTROL {label}: rejected (exit 1)')
        # Native compute runs the same vector light function injected into all
        # six fragment variants, including its sun sign, shadow and lamp phase.
        shader = tmp / 'phase.metal'
        shader.write_text('#include <metal_stdlib>\nusing namespace metal;\n'
            'struct MatConsts {float4 k[12];};\n' + msl + '''
kernel void evaluate(device float4 *out [[buffer(0)]], constant MatConsts &m [[buffer(1)]],
                     constant ParticleLamps &lamps [[buffer(2)]]) {
  out[0]=float4(particle_light(float4(.5f,.5f,.5f,1.0f),m,lamps,1.0f),1.0f);
  out[1]=float4(particle_light(float4(.5f,.5f,.5f,1.0f),m,lamps,0.0f),1.0f);
}
''')
        native = tmp / 'native.m'
        native.write_text(r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
typedef struct {float k[12][4];} Constants;
typedef struct {float k[17][4];} Lamps;
static float phase(float mu){float g=.682f;return (1-g*g)/powf(1+g*g-2*g*mu,1.5f);}
int main(int argc,char **argv){@autoreleasepool {
  id<MTLDevice> device=MTLCreateSystemDefaultDevice();
  if(!device){puts("SKIP native particles: no Metal device");return 3;}
  NSError *error=nil;
  NSString *source=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error];
  id<MTLLibrary> lib=[device newLibraryWithSource:source options:nil error:&error];
  if(!lib){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 1;}
  id<MTLComputePipelineState> pipeline=[device newComputePipelineStateWithFunction:[lib newFunctionWithName:@"evaluate"] error:&error];
  if(!pipeline)return 1;
  id<MTLCommandQueue> queue=[device newCommandQueue];int checks=0;
  for(int test=0;test<3;++test){
    Constants m={0};Lamps lamps={0};
    m.k[3][0]=2;m.k[3][1]=-2;m.k[11][0]=-1;m.k[11][1]=1;
    for(int i=0;i<4;++i)m.k[4+i][i]=1;
    m.k[8][3]=.682f;m.k[9][3]=.93f;m.k[10][2]=test==1?-1:1;
    for(int i=0;i<3;++i){m.k[8][i]=test==2?0:.2f;m.k[9][i]=test==2?0:.5f;}
    if(test==2){lamps.k[0][0]=1;lamps.k[1][2]=1;lamps.k[1][3]=1;lamps.k[2][2]=-1;lamps.k[2][3]=60;}
    id<MTLBuffer> output=[device newBufferWithLength:32 options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLComputeCommandEncoder> e=[command computeCommandEncoder];
    [e setComputePipelineState:pipeline];[e setBuffer:output offset:0 atIndex:0];
    [e setBytes:&m length:sizeof m atIndex:1];[e setBytes:&lamps length:sizeof lamps atIndex:2];
    [e dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];[e endEncoding];
    [command commit];[command waitUntilCompleted];if(command.status!=MTLCommandBufferStatusCompleted)return 1;
    float *v=output.contents;
    float expected=.93f*(.2f+.5f*phase(test==1?-1:1)), dark=.93f*.2f;
    if(test==2)expected=dark=.93f*powf(1-.5f/60,2)/(1+.25f/64)*phase(1);
    for(int c=0;c<3;++c){
      if(!isfinite(v[c]) || fabsf(v[c]-expected)>expected*.0002f+1e-5f || fabsf(v[4+c]-dark)>dark*.0002f+1e-5f){
        fprintf(stderr,"native test %d channel %d: %g/%g expected %g/%g\n",test,c,v[c],v[4+c],expected,dark);return 1;}
      checks+=2;
    }
  }
  printf("PASS native production lighting: %d RGB checks (forward/back sun, shadow, headlight)\n",checks);
  return 0;
}}
''')
        run(['clang', '-fobjc-arc', native, '-framework', 'Foundation', '-framework', 'Metal', '-o', binary])
        result = subprocess.run([str(binary), str(shader)], capture_output=True, text=True)
        print(result.stdout.strip())
        assert result.returncode in (0, 3), result.stderr[-3000:]
        if result.returncode == 3:
            raise SystemExit(3)


if __name__ == '__main__':
    main()
