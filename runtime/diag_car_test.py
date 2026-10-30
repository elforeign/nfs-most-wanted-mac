"""Original translated Render + synthetic CarRenderInfo, no game or emulation.

Proof: normal 616/616; swapped reason 2 failures; missing namespace 151;
removed off guard 1 (off=2 helper entries/11 guest queries/1 log). It also
counts diagnostic entries/guest queries with BOOT_DIAG off, checks sampling,
connection hashes, unreached fields, view exemptions and original CPU results.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'recomp'))
from car_render_diag import car_render_diagnostics

HARNESS = r'''
#include "host.h"
#include "funcs.h"
#include "diag_car.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
int diag_on = 1;
static uint64_t frame_now;
static unsigned entries, queries, logs, failures, checks, original_calls;
static char last_line[24576];
static int pixels=500, visibility=1, camera_inside, view_mode, allocation_fail;
static uint32_t matrix_next;
static const uint32_t self=0x10000, view=0x20000, ride=0x30000, sp=0x100000;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"line %d: %s\n",__LINE__,#x); } } while(0)
void diag_event(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt); vsnprintf(last_line,sizeof last_line,fmt,ap); va_end(ap); ++logs;
}
FILE *dump_file(void) { return NULL; }
#define diag_car_init real_diag_car_init
#define diag_car_begin real_diag_car_begin
#define diag_car_observe real_diag_car_observe
#define diag_car_reject real_diag_car_reject
#define diag_car_end real_diag_car_end
#include "DIAG_SOURCE"
#undef diag_car_init
#undef diag_car_begin
#undef diag_car_observe
#undef diag_car_reject
#undef diag_car_end
void diag_car_init(void) { ++entries; real_diag_car_init(); }
void diag_car_begin(Cpu *c, DiagCarRender *s, uint64_t f) { ++entries; real_diag_car_begin(c,s,f); }
void diag_car_observe(Cpu *c, uint32_t site) { ++entries; real_diag_car_observe(c,site); }
void diag_car_reject(uint32_t site) { ++entries; real_diag_car_reject(site); }
void diag_car_end(Cpu *c, DiagCarRender *s) { ++entries; real_diag_car_end(c,s); }
typedef struct { uint32_t type, solid_count, *solid_map; } Addition;
static Addition additions[2];
static unsigned nadditions=1;
static _Thread_local Addition *model_namespace;
static int on(void) { return 1; }
static Addition *addition_for_type(unsigned type) { return type==102 ? &additions[0] : NULL; }
void f_00751540_orig(Cpu *c) {
    CHECK(arg(c,0)==0);
    if(c->ecx==0x40000) c->eax=0xE826B87B+arg(c,1);
    else { CHECK(c->ecx==0x40100 && arg(c,1)==0); c->eax=0x12345677; }
    c->esp+=12;
}
HASH_FUNCTIONS
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    ++queries;
    if(fn==0x00739B40) {
        CHECK(c->ecx==ride && n==4);
        M32(a[1])=0; M32(a[2])=0;
        return a[0]==0x46;            // the fixed-LOD light slot
    }
    CHECK(fn==0x00751540 && n==2 && a[0]==0);
    CHECK(model_namespace==&additions[0]);
    uint32_t saved_sp=c->esp;
    for(int i=n-1;i>=0;--i) { c->esp-=4; M32(c->esp)=a[i]; }
    c->esp-=4; M32(c->esp)=0;
    f_00751540(c); c->esp=saved_sp;
    return c->eax;
}
uint32_t split_shadow_id(uint32_t p) { return M32(p+4); }
void bad_jump(Cpu *c, uint32_t p) { (void)c;(void)p; abort(); }
void dispatch(Cpu *c, uint32_t p) { (void)c;(void)p; abort(); }
void untranslated(Cpu *c, uint32_t p) { (void)c;(void)p; abort(); }
static void stub(Cpu *c, uint32_t fn, unsigned pop) {
    ++original_calls;
    switch(fn) {
    case 0x004FCAC0: c->eax=pixels; break;
    case 0x006BF530: c->eax=view_mode; break;
    case 0x00405ED0: c->eax=allocation_fail ? 0 : (matrix_next+=64); break;
    case 0x004FCA70: c->eax=visibility; break;
    case 0x0045F340: x87_push(c,1.0); break;
    case 0x0045FDE0: c->eax=camera_inside; break;
    case 0x007C4B80: c->eax=(uint32_t)(int32_t)ST(0); x87_pop(c); break;
    default: c->eax=0; break;
    }
    c->esp+=4+pop;
}
STUBS
ORIGINAL
WRAPPER
static Cpu render(void) {
    matrix_next=0x60000;
    Cpu c={0}; c.ecx=self; c.esp=sp; c.ebx=0x111; c.esi=0x222; c.edi=0x333; c.ebp=0x444; c.cw=0x37f;
    M32(sp)=0; M32(sp+4)=view; M32(sp+8)=0x50000; M32(sp+12)=0x50100;
    f_0074E160(&c);
    CHECK(c.esp==sp+52 && c.ebx==0x111 && c.esi==0x222 && c.edi==0x333 && c.ebp==0x444);
    CHECK(model_namespace==&additions[1]);
    return c;
}
static void fixture(void) {
    memset(G,0,0xa00000);
    M32(self+0x84)=ride; M32(ride)=102;
    M32(0x009B09D8)=0x70000; strcpy((char *)G+0x70000+102*0xd0,"JAGUARXK");
    M32(ride+0x48)=0x40000; M32(ride+0x48+0x46*4)=0x40100;
    M32(view+4)=1; M32(view+0x24)=2; M32(view+0x40)=0x50200;
    M32(self+0x1628)=0; M32(self+0x162c)=4;
    MF32(self+0x90)=-1; MF32(self+0x94)=-2; MF32(self+0x98)=-3;
    MF32(self+0xa0)=1; MF32(self+0xa4)=2; MF32(self+0xa8)=3; MF32(self+0x17a8)=4;
    M32(self+0xb84)=0x80003; M32(0x80008)=0xE826B97B; M32(0x8000c)=0x81000;
    M32(0x00903320)=1; M32(0x00903384)=UINT32_MAX; M32(0x00903388)=UINT32_MAX;
    M32(0x00915f84)=0x90000; M32(0x00915f88)=0xa0000;
    MF32(0x00890e98)=4; MF32(0x008b2ee8)=0.5; MF32(0x008933d4)=0.5;
    M8(self+0x17c0)=1; MF32(self+0x17c4)=1;  // original true edge after the four candidate tests
    pixels=500; visibility=1; camera_inside=0; view_mode=0; allocation_fail=0;
    frame_now+=120; logs=queries=entries=original_calls=0; last_line[0]=0;
}
static void expect(const char *why, unsigned result) {
    Cpu c=render();
    CHECK((c.eax&255)==result && logs==1 && original_calls>0);
    char expected[64]; snprintf(expected,sizeof expected,"reason=%s ",why); CHECK(strstr(last_line,expected));
    CHECK(strstr(last_line,"car=JAGUARXK type=102 view=1"));
    CHECK(strstr(last_line,"bounds=(-1,-2,-3)..(1,2,3) radius=4"));
    CHECK(strstr(last_line,"L0[00:E826B97B->found@00080000/00081000"));
    CHECK(strstr(last_line,"L1[00:E826B97C->null@00000000/00000000"));
    CHECK(strstr(last_line,"46:12345678->null"));
    printf("case %s: result=%u %s\n",why,c.eax&255,last_line);
}
int main(void) {
    G=calloc(1,0xa00000); if(!G)return 2;
    uint32_t map[]={0x12345677,0x12345678,0xE826B87B,0xE826B97B,0xE826B87C,0xE826B97C,
                    0xE826B87D,0xE826B97D,0xE826B87E,0xE826B97E,0xE826B87F,0xE826B97F};
    additions[0].solid_count=6; additions[0].solid_map=map; model_namespace=&additions[1];
    diag_car_init();
    fixture(); pixels=1; visibility=0; M32(self+0x1628)=M32(self+0x162c)=UINT32_MAX; camera_inside=1;
    expect("pixel-size",0); CHECK(strstr(last_line,"pixel=1 minpixel=2")); CHECK(strstr(last_line,"chosen=na/na visible=na camera_inside=na"));
    fixture(); visibility=0; expect("visibility",0); CHECK(strstr(last_line,"visible=0 camera_inside=na"));
    fixture(); M32(self+0x1628)=M32(self+0x162c)=UINT32_MAX; expect("lod",0); CHECK(strstr(last_line,"chosen=-1/-1 visible=1"));
    fixture(); camera_inside=1; expect("camera-inside",0); CHECK(strstr(last_line,"chosen=0/0 visible=1 camera_inside=1"));
    fixture(); expect("none",1); CHECK(strstr(last_line,"camera_inside=0"));
    fixture(); allocation_fail=1; expect("frame-allocation",0);
    fixture(); pixels=1; M32(view+4)=15; Cpu c=render(); CHECK((c.eax&255)==1 && strstr(last_line,"reason=none"));
    fixture(); pixels=1; M32(view+4)=16; c=render(); CHECK((c.eax&255)==1 && strstr(last_line,"reason=none"));
    fixture(); pixels=3; view_mode=3; expect("pixel-size",0); CHECK(strstr(last_line,"pixel=1 minpixel=2"));
    fixture(); M32(0x00903384)=UINT32_MAX; M32(self+0x1628)=M32(self+0x162c)=UINT32_MAX;
    M32(0x00903388)=0; expect("none",1); CHECK(strstr(last_line,"chosen=-1/0"));
    fixture(); c=render(); unsigned first_logs=logs,first_queries=queries;
    ++frame_now; render(); CHECK(logs==first_logs && queries==first_queries);
    frame_now+=119; render(); CHECK(logs==first_logs+1 && queries>first_queries);
    M32(view+4)=4; render(); CHECK(logs==first_logs+2); // independent view sample
    M32(view+4)=1; ++M32(sp+4+8*4); render(); CHECK(logs==first_logs+3); // reflection sample
    fixture(); diag_on=0; Cpu off=render();
    unsigned off_entries=entries, off_queries=queries, off_logs=logs;
    CHECK(off_entries==0 && off_queries==0 && off_logs==0);
    fixture(); diag_on=1; Cpu on=render(); CHECK(!memcmp(&off,&on,sizeof on));
    printf("CAR DIAG %s: %u checks, %u failures; off=%u/%u/%u entries/queries/logs\n",
           failures?"FAIL":"PASS",checks,failures,off_entries,off_queries,off_logs);
    free(G); return failures!=0;
}
'''


def decoder():
    path = ROOT / 'recomp/out/speed_47.c'
    if not path.exists():
        print('SKIP: build the recompiler first')
        raise SystemExit(3)
    source = path.read_text()
    match = re.search(r'void f_0074E160_orig\(Cpu \*restrict c\) \{', source)
    assert match, 'missing original Render'
    end = source.index('\n__attribute__((weak))', match.end())
    code = source[match.start():end]
    # Prove the generated body uses today's transformation and fails closed on drift.
    original = re.sub(r'    if \(diag_on\) diag_car_observe\(c, 0x[0-9A-F]+u\);\n', '', code)
    original = re.sub(r'if \((!?zf)\) \{ if \(diag_on\) diag_car_reject\(0x[0-9A-F]+u\); goto L_0075052A; \}',
                      r'if (\1) goto L_0075052A;', original)
    assert car_render_diagnostics(0x0074E160, original) == code, 'stale diagnostic instrumentation'
    try:
        car_render_diagnostics(0x0074E160, original.replace('0074E694: test eax, eax', '0074E694: test ebx, ebx'))
    except AssertionError:
        pass
    else:
        raise AssertionError('instruction drift was accepted')
    pops = {'004FCAC0': 8, '004FCA70': 12}
    names = sorted(set(re.findall(r'\bf_([0-9A-F]{8})\(c\)', code)))
    stubs = '\n'.join(f'void f_{name}(Cpu *c) {{ stub(c,0x{name}u,{pops.get(name, 0)}); }}' for name in names)
    return code, stubs


def main():
    code, stubs = decoder()
    diag = (ROOT / 'runtime/diag.c').read_text()
    wrapper = diag[diag.index('\nunsigned diag_car1[4];') + 1:diag.index('void f_007387E0_orig')]
    assert 'if (!diag_on) { f_0074E160_orig(c); return; }' in wrapper
    source = (ROOT / 'runtime/diag_car.c').read_text()
    cars = (ROOT / 'runtime/car85.c').read_text()
    names = ['solid_context', 'private_solid_hash', 'model_00751540', 'f_00751540', 'car85_diag_model_hash']
    functions = []
    for name in names:
        match = re.search(r'^[^\n;]*\b' + name + r'\([^\n]*\) \{\n', cars, re.M)
        assert match, name
        functions.append(cars[match.start():cars.index('\n}', match.end()) + 2])
    hashes = '\n'.join(functions)
    with tempfile.TemporaryDirectory(prefix='car-diag-') as td:
        tmp = Path(td)
        mutant = source.replace('case 0x0074E223: return "pixel-size";', 'case 0x0074E223: return "visibility";')
        assert mutant != source
        no_scope = hashes.replace('model_namespace = nadditions ? solid_context(ride) : NULL;', 'model_namespace = NULL;')
        no_guard = wrapper.replace('if (!diag_on) { f_0074E160_orig(c); return; }', '')
        assert no_scope != hashes and no_guard != wrapper
        variants = [('normal', source, hashes, wrapper), ('swapped-reason', mutant, hashes, wrapper),
                    ('no-namespace', source, no_scope, wrapper), ('off-guard-removed', source, hashes, no_guard)]
        for name, text, scope, hook in variants:
            unit = tmp / (name + '.c')
            unit.write_text(text)
            harness = tmp / 'harness.c'
            harness.write_text(HARNESS.replace('DIAG_SOURCE', str(unit)).replace('STUBS', stubs)
                               .replace('ORIGINAL', code).replace('WRAPPER', hook).replace('HASH_FUNCTIONS', scope))
            exe = tmp / name
            p = subprocess.run(['clang', '-O1', '-g', '-fsanitize=address,undefined', '-Iruntime', '-Irecomp',
                                '-Irecomp/out', str(harness), '-o', str(exe)], cwd=ROOT, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-3000:]
            p = subprocess.run([str(exe)], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
            print(name, p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-2000:], flush=True)
            if name == 'normal' and p.returncode:
                print(p.stderr[-3000:])
            assert p.returncode == (0 if name == 'normal' else 1), (name, p.stderr[-3000:])
            # Keep the proof's actual printed lines available in suite logs.
            if name == 'normal':
                print(p.stdout, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
