"""Offline chase-camera maths and hook isolation, including two broken controls.
Run: $TOOLS_PY -I runtime/chasecam_test.py [--broken]
Compiles the real chasecam.c with the translated MW parameter/boost evaluators
and their dependency closure, loading their actual tables from speed.exe.
Curve expectations come from the decoded endpoints in chasecam_spec.md.
The live recorded matrix proof is BOOT_DIAG_CHASE_COMPARE=1 (or broken).
Results: 417 hook/registry checks, 12 env checks and 104 curve comparisons pass; flattened close FOV
fails 1 hook check and 9 curve comparisons; applying On hooks while Off
fails 12 hook checks. The runtime matrix comparator gives 0 differing words normally
and 1 with deliberate output corruption. The normal run rejects all three broken controls.
No game, Unicorn, GPU, sockets or private ProStreet input is used.
"""
import math
import os
import csv
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'speed.exe'
SPEEDS = [-10, 0, 8.05, 32.19, 48.28, 50, 80.47, 160.93, 193.12, 200, 241.4, 257.5, 300]

HARNESS = r'''
#include <stdlib.h>
#include "chasecam.c"
uint8_t *G; int diag_on; const char *diag_dir = "/tmp";
const char *game_dir;
static char saved_value[16];
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key,const char *value) { (void)key; snprintf(saved_value,sizeof saved_value,"%s",value); }
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void report_note(const char *f, ...) { (void)f; }
void diag_event(const char *f, ...) { (void)f; }
float diag_car_speed(void) { return 0; }
uint64_t diag_current_frame(void) { return 0; }
void diag_shot_request(uint64_t f) { (void)f; }
void bad_jump(Cpu *c, uint32_t a) { (void)c; fprintf(stderr,"bad jump %x\n",a); abort(); }
void dispatch(Cpu *c, uint32_t a) { (void)c; (void)a; abort(); }
void f_0042F140_orig(Cpu *c) { c->esp += 8; }
enum { MOVER=0xB00000, ANCHOR=0xB01000, CAMERA=0xB02000, PARAM=0xB03000,
       VECTOR=0xB04000, STACK=0xD00000, SIZE=0x1000000 };
static unsigned checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { if (failures < 6) fprintf(stderr,"check line %d failed: %s\n",__LINE__,#x); ++failures; } } while (0)
static void near(float a, float b) { CHECK(isfinite(a) && fabsf(a-b) < .00003f); }
void f_004713C0_orig(Cpu *c) {
    uint32_t mode=arg(c,0), p=c->ecx+0xC8;
    M16(p)=mode; M16(p+2)=0;
    MF32(p+4)=mode==3 ? -5.436f : -4.7f;
    MF32(p+8)=mode==3 ? 1.7f : 1.62f; MF32(p+12)=.125f;
    M16(p+16)=(uint16_t)((mode==3 ? 90.f : 78.f)*65536.f/360.f);
    c->eax=p; c->ecx=0x12345678; c->esp+=8;
}
static void child(Cpu *q, uint32_t self, uint32_t ret) {
    memset(q,0,sizeof *q); q->cw=0x37F; q->ecx=self; q->esp=STACK;
    M32(q->esp)=ret;
}
// Orchestrate the three real hooks with MW's actual parameter/boost code. This
// is a hook-isolation fixture, not the live CubicCameraMover/matrix proof.
void f_0047D3C0_orig(Cpu *c) {
    uint32_t mover=c->ecx, anchor=M32(mover+0x80), mode=M32(mover+0x98);
    Cpu q; child(&q,anchor,0x0047D42E); M32(STACK+4)=mode; f_004713C0(&q);
    uint32_t p=q.eax;
    child(&q,0x8EE040+mode*20,0x0047D4B1); M32(STACK+4)=PARAM;
    MF32(STACK+8)=fminf(1,fmaxf(0,MF32(anchor+0x10)/71.f)); f_0046F2B0(&q);
    MF32(VECTOR)=MF32(p+4); MF32(VECTOR+4)=MF32(p+12); MF32(VECTOR+8)=MF32(p+8);
    child(&q,mover,0x0047CFDC); M32(STACK+4)=VECTOR; f_0047CE80(&q);
    memcpy(G+CAMERA,G+VECTOR,12); M32(CAMERA+12)=M16(p+16);
    c->eax=p; c->esp+=8;
}
static Cpu initial(unsigned view, unsigned mode, float kmh) {
    M32(MOVER+0x10)=view; M32(MOVER+0x1C)=CAMERA;
    M32(MOVER+0x80)=ANCHOR; M32(MOVER+0x98)=mode;
    M32(0x9195E0+view*0x70+0x40)=CAMERA;
    MF32(ANCHOR+0x10)=kmh/3.6f; MF32(ANCHOR+0x14)=80;
    Cpu c; child(&c,MOVER,0x12345); return c;
}
int main(int argc,char **argv) {
    if(argc!=2 && argc!=3) return 2;
    G=calloc(1,SIZE); FILE *f=fopen(argv[1],"rb"); if(!G || !f) return 2;
    size_t got=fread(G,1,SIZE,f); fclose(f); if(!got) return 2;
    Setting *registered=settings_find("controls.chase_camera");
    CHECK(registered==&s_chase && registered->category==SET_CONTROLS && registered->order==140);
    CHECK(registered->def==0 && registered->restart==0 && !strcmp(registered->env,"NFS_CHASE_CAM"));
    settings_init();
    if(argc==3) {
        int expected=atoi(argv[2]);
        CHECK(registered->locked); CHECK(settings_get(registered->id)==expected);
        CHECK(!settings_set(registered,!expected)); CHECK(settings_get(registered->id)==expected);
        printf("env expected=%d checks=%u failures=%u\n",expected,checks,failures);
        free(G); return failures?1:0;
    }
    CHECK(!registered->locked); CHECK(settings_get(registered->id)==0);
    CHECK(settings_set(registered,1)); CHECK(settings_get(registered->id)==1); CHECK(!strcmp(saved_value,"on"));
    CHECK(settings_set(registered,0)); CHECK(settings_get(registered->id)==0); CHECK(!strcmp(saved_value,"off"));
    uint8_t *before=malloc(SIZE), *after=malloc(SIZE);
    for(unsigned diagnostics=0;diagnostics<2;++diagnostics) {
    diag_on=diagnostics;
    for(unsigned mode=2;mode<=3;++mode) for(unsigned v=0;v<4;++v) {
        float kmh=(float[]){0,50,200,300}[v];
        for(unsigned view=1;view<=4;++view) {
            Cpu c=initial(view,mode,kmh), baseline=c; s_chase.value=0;
            memcpy(before,G,SIZE); f_0047D3C0_orig(&baseline); memcpy(after,G,SIZE);
            memcpy(G,before,SIZE); f_0047D3C0(&c);
            CHECK(!memcmp(&c,&baseline,sizeof c)); CHECK(!memcmp(G,after,SIZE)); CHECK(frame==NULL);
        }
        Cpu c=initial(1,mode,kmh); s_chase.value=1;
        uint8_t vibration[80]; memcpy(vibration,G+0x8EDFB0,80);
        f_0047D3C0(&c); ChaseCurve r=chase_curve(mode==3,kmh);
        near(MF32(VECTOR), (mode==3 ? -5.436f : -4.7f)*r.distance_ratio);
        near(MF32(VECTOR+8), (mode==3 ? 1.7f : 1.62f)*r.height_ratio);
        CHECK(MF32(VECTOR+4)==.125f); // no side movement
        for(unsigned i=0;i<3;++i) near(MF32(PARAM+0xA0+4*i),.4f*r.direction_ratio);
        CHECK(!memcmp(vibration,G+0x8EDFB0,80)); CHECK(frame==NULL);
        uint32_t p=ANCHOR+0xC8;
        unsigned base=(unsigned)((mode==3 ? 90.f:78.f)*65536.f/360.f);
        CHECK(abs((int)M16(p+16)-(int)(base+r.fov_delta*65536.f/360.f))<=1);
        // Unrelated POV and view, even On, must still delegate byte for byte.
        for(unsigned variant=0;variant<2;++variant) {
            c=initial(variant?4:1,variant?mode:1,kmh); Cpu baseline=c;
            memcpy(before,G,SIZE); f_0047D3C0_orig(&baseline); memcpy(after,G,SIZE);
            memcpy(G,before,SIZE); f_0047D3C0(&c);
            CHECK(!memcmp(&c,&baseline,sizeof c)); CHECK(!memcmp(G,after,SIZE));
        }
    }
    }
    // Direct Off wrappers preserve the actual guest evaluator's CPU, x87 and all guest bytes.
    for(unsigned which=0;which<2;++which) {
        Cpu c; initial(1,2,200); child(&c,which?MOVER:0x8EE068,0x11111);
        M32(STACK+4)=which?VECTOR:PARAM; MF32(STACK+8)=.78f;
        MF32(VECTOR)=-4.7f; MF32(VECTOR+8)=1.62f; Cpu baseline=c;
        memcpy(before,G,SIZE);
        if(which) f_0047CE80_orig(&baseline); else f_0046F2B0_orig(&baseline);
        memcpy(after,G,SIZE); memcpy(G,before,SIZE);
        if(which) f_0047CE80(&c); else f_0046F2B0(&c);
        CHECK(!memcmp(&c,&baseline,sizeof c)); CHECK(!memcmp(G,after,SIZE));
    }
    static const float speeds[]={-10,0,8.05f,32.19f,48.28f,50,80.47f,160.93f,193.12f,200,241.4f,257.5f,300};
    for(int far=0;far<2;++far) for(unsigned i=0;i<sizeof speeds/sizeof *speeds;++i) {
        ChaseCurve r=chase_curve(far,speeds[i]);
        printf("curve %d %u %.9g %.9g %.9g %.9g\n",far,i,r.fov_delta,r.distance_ratio,r.height_ratio,r.direction_ratio);
    }
    ChaseCurve nan=chase_curve(0,NAN), neg=chase_curve(1,-INFINITY), hi=chase_curve(0,INFINITY);
    near(nan.fov_delta,0); near(neg.distance_ratio,1); near(hi.fov_delta,37);
    // Exercise the exact runtime matrix comparator and its output-corruption
    // control offline as well: a target-only fault is delayed by MW one tick.
    static char dir[1024]; snprintf(dir,sizeof dir,"%s",argv[1]);
    char *slash=strrchr(dir,'/'); if(!slash) return 2; *slash=0; diag_dir=dir;
    Cpu replay=initial(1,2,200); s_chase.value=0; diag_on=1;
    ChaseFrame proof={.mover=MOVER,.anchor=ANCHOR,.camera=CAMERA,.mode=2};
    frame=&proof; chase_compare(&replay,&proof,0);
    replay=initial(1,2,200); chase_compare(&replay,&proof,1); frame=NULL;
    printf("hooks checks=%u failures=%u\n",checks,failures);
    free(before); free(after); free(G); return failures?1:0;
}
'''


def translated():
    bodies = {}
    for p in (ROOT / 'recomp/out').glob('speed_*.c'):
        for m in re.finditer(r'^void f_([0-9A-F]{8})(?:_orig)?\(Cpu \*restrict c\) \{.*?^}', p.read_text(), re.M | re.S):
            bodies[m[1]] = m[0]
    needed = {'0047CE80', '0046F2B0', '00431C80'}
    todo = list(needed)
    while todo:
        for d in set(re.findall(r'f_([0-9A-F]{8})(?:_orig)?\(c', bodies[todo.pop()])) - needed:
            needed.add(d)
            todo.append(d)
    declarations = '\n'.join(f'void f_{n}(Cpu *c);' for n in needed)
    # When run before the branch's recompiler build, the same retail bodies do
    # not yet have _orig names. Make extraction independent of that build order.
    code = '\n'.join(bodies[n] for n in sorted(needed))
    for n in ('0047CE80', '0046F2B0', '00431C80'):
        code = re.sub(rf'void f_{n}(?=\()', f'void f_{n}_orig', code)
    return '#include "runtime.h"\n' + declarations + '\n' + code


def image():
    b = EXE.read_bytes()
    pe = struct.unpack_from('<I', b, 0x3c)[0]
    ns, osz = struct.unpack_from('<H', b, pe + 6)[0], struct.unpack_from('<H', b, pe + 20)[0]
    memory = bytearray(0xB00000)
    for i in range(ns):
        _, vs, va, rs, ro = struct.unpack_from('<8sIIII', b, pe + 24 + osz + i * 40)
        memory[0x400000 + va:0x400000 + va + rs] = b[ro:ro + rs]
    return memory


def expect(far, speed):
    def interp(a, b, low, high):
        return a + (b-a) * min(1., max(0., (speed-low)/(high-low)))
    if far:
        return (interp(48, 95, 8.05, 193.12)-48, interp(6.5, 7.2, 8.05, 241.4)/6.5,
                interp(1.4, 1.25, 48.28, 160.93)/1.4, 1.)
    return (interp(48, 85, 32.19, 257.5)-48, interp(5.8, 5.7, 32.19, 257.5)/5.8,
            interp(1.6, 1.5, 32.19, 257.5)/1.6, interp(.2, .1, 0, 80.47)/.2)


def run(d, label, source):
    (d / 'chasecam.c').write_text(source)
    result = subprocess.run(['clang', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
        '-I', str(d), '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'),
        str(d / 'harness.c'), str(d / 'retail.c'), str(ROOT / 'runtime/settings.c'),
        '-o', str(d / 'test')], capture_output=True, text=True)
    if result.returncode:
        print(result.stderr[-4000:])
        raise RuntimeError('harness compile failed')
    env = {k:v for k,v in os.environ.items() if k != 'NFS_CHASE_CAM' and not k.startswith('BOOT_DIAG_CHASE')}
    p = subprocess.run([str(d / 'test'), str(d / 'image.bin')], capture_output=True, text=True, env=env)
    failures = int(p.returncode != 0)
    rows = 0
    for line in p.stdout.splitlines():
        if line.startswith('curve '):
            _, far, index, *values = line.split()
            expected = expect(int(far), SPEEDS[int(index)])
            failures += sum(not math.isclose(float(a), b, rel_tol=1e-6, abs_tol=1e-5) for a, b in zip(values, expected))
            rows += 1
        elif line.startswith('hooks '):
            print(label, line)
    failures += int(rows != len(SPEEDS) * 2)
    proof = list(csv.DictReader((d / 'chasecam_compare.tsv').open(), delimiter='\t'))
    failures += int(len(proof) != 2 or proof[0]['different_words'] != '0' or proof[1]['different_words'] != '1')
    if label == 'normal':
        for value in ('off', 'on'):
            p_env = subprocess.run([str(d / 'test'), str(d / 'image.bin'), str(int(value == 'on'))],
                capture_output=True, text=True, env={**env, 'NFS_CHASE_CAM':value})
            print(p_env.stdout.strip())
            failures += int(p_env.returncode != 0)
    if p.stderr and label == 'normal':
        print(p.stderr[-2000:])
    print(f'{label}: curve_values={rows*4} failed_comparisons={failures}')
    return failures


def check_recording():
    path = ROOT / 'run_shots/chasecam_off_matrices.tsv'
    if not path.exists():
        return True  # source-only/public checkouts can still test the hooks
    normal = 0
    for row in csv.DictReader(path.open(), delimiter='\t'):
        a, b = row['reference_hex'], row['off_hex']
        if not re.fullmatch('[0-9a-f]{128}', a) or not re.fullmatch('[0-9a-f]{128}', b):
            return False
        words = sum(a[i:i+8] != b[i:i+8] for i in range(0, 128, 8))
        words += row['fov_reference'] != row['fov_off']
        if words != int(row['different_words']):
            return False
        if row['broken'] != '0' or words:
            return False
        normal += 1
    print(f'recorded: {normal} exact matrix/FOV pairs; runtime comparator broken control rejects 1 corrupted word')
    return normal > 0


def main():
    if not EXE.exists() or not (ROOT / 'recomp/out/speed_08.c').exists():
        print('SKIP: needs MW speed.exe and translated camera code')
        return 3
    with tempfile.TemporaryDirectory(prefix='chasecam-test-') as t:
        d = Path(t)
        (d / 'harness.c').write_text(HARNESS)
        (d / 'retail.c').write_text(translated())
        (d / 'image.bin').write_bytes(image())
        source = (ROOT / 'runtime/chasecam.c').read_text()
        broken = source.replace('r.fov_delta = 37.f * t;', 'r.fov_delta = 0.f * t;')
        if '--broken' in sys.argv:
            return int(bool(run(d, 'broken-curve', broken)))
        normal = run(d, 'normal', source)
        mutant_curve = run(d, 'broken-curve', broken)
        mutant_off = run(d, 'broken-Off', source.replace('if (frame->active) {', 'if (1) {'))
        ok = normal == 0 and mutant_curve > 0 and mutant_off > 0 and check_recording()
        print('CHASECAM PASS (both broken controls rejected)' if ok else 'CHASECAM FAIL')
        return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
