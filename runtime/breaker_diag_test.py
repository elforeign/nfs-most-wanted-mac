"""Retail translated camera decisions through the production Load wrapper (P2).
Proof: normal 151 checks / 0 failures; Load-hook reversion 13 checks / 1 failure
(skip: load not reached); disabled-observer mutant 4 checks / 1 failure.
Off-path predecessor comparisons are equivalence evidence, not a bug fix.
No game, sockets, GPU or emulation.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'recomp'))
import recomp
import build_all

HARNESS = r'''
#include "host.h"
#include "funcs.h"
#include <stdlib.h>
#include <stdarg.h>
#include <assert.h>
uint8_t *G;
int diag_on=1;
static char logs[4096];
static unsigned lines, loads, checks, observes;
static int factory=1, interface=1, allocation=1, rigid=1, split, pursuit=1, busted, missing_player, missing_sim, active=1, tracked=1, matching=1;
static float now=1, distance=-10, score=5;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr,"checks=%u failures=1; line %d: %s; %s\n",checks,__LINE__,#x,logs); exit(1); } } while(0)
void diag_event(const char *fmt,...) { va_list ap; va_start(ap,fmt); vsnprintf(logs,sizeof logs,fmt,ap); va_end(ap); ++lines; }
void bad_jump(Cpu *c,uint32_t p) { (void)c; fprintf(stderr,"bad jump %x\n",p); abort(); }
static void unexpected(uint32_t p) { fprintf(stderr,"unexpected %x\n",p); abort(); }
STUBS
TRANSLATED
#include "breaker_diag.c"
static uint32_t nis_activity, nis_hash;
static char nis_name[64];
static unsigned nis_meta=64; // Suppress unrelated NIS metadata lines.
static int nis_playing;
uint32_t galloc(uint32_t n) { (void)n; unexpected(0); return 0; }
#include "breaker_load_hook.inc"
void dispatch(Cpu *c,uint32_t fn) {
    switch(fn) {
    case 1: c->eax=missing_sim?0:0x12000; break; // player simable
    case 2: c->eax=0x13000; break; // player
    case 3: c->eax=0x14000; break; // bust state
    case 4: c->eax=busted; break;
    case 5: c->eax=active; break;
    case 6: c->eax=0x15000; break; // CAR key
    case 7: c->eax=tracked?0x16000:0; break;
    case 8: c->eax=(c->ecx==0x17000 && !matching)?2:1; break;
    case 9: c->eax=0x18000; break; // position
    case 10: c->eax=0x19000; MF32(c->eax)=distance; break;
    case 11: c->eax=rigid?0x21000:0; break;
    case 13: c->esp+=4; break;
    case 14: c->esp+=8; break;
    case 15: MF32(arg(c,0))=1; c->esp+=4; break;
    case 12: ++loads; f_006F51F0(c); return;
    default: unexpected(fn);
    }
    c->esp+=4;
}
static Cpu run(int on,int original) {
    memset(G+0x700000,0,0x100000); memset(G+0x20000,0,0x1000);
    Cpu c={0}; c.esp=0x780000; c.ecx=0x20000; c.cw=0x37f;
    M32(c.esp+4)=0x11000; M32(c.esp+8)=0x30000; M32(c.esp+12)=0x30100;
    diag_on=on; logs[0]=0; lines=loads=observes=0;
    if (original) f_00640F00_orig(&c); else f_00640F00(&c);
    CHECK(c.esp==0x780020); return c;
}
static void expect(const char *s) { CHECK(lines==1); CHECK(observes>0); CHECK(strstr(logs,s)); CHECK(strstr(logs,"scene WMGasStation")); }
int main(int argc,char **argv) {
    unsetenv("BOOT_NIS");
    (void)argc; FILE *fp=fopen(argv[1],"rb"); G=calloc(1,0x1000000); CHECK(fp); CHECK(fread(G,1,0x1000000,fp)==0x1000000); fclose(fp);
    strcpy((char*)G+0x30000,"WMGasStation"); strcpy((char*)G+0x30100,"Moment");
    M32(0x91cf90)=0x40000; M32(0x40010)=0x41000; M8(0x41047)=1;
    M32(0x91e000)=0x42000; M32(0x43968)=0x43000;
    M32(0x92ce9c)=0x44000; M32(0x44000)=0x1a000;
    M32(0x11000)=0x50000; M32(0x50004)=1; M32(0x500ac)=2;
    M32(0x13000)=0x51000; M32(0x510a0)=3;
    M32(0x14000)=0x52000; M32(0x52074)=4;
    M32(0x1a000)=0x53000; M32(0x53088)=5; M32(0x53044)=6; M32(0x5300c)=10;
    M32(0x15000)=M32(0x92c52c);
    M32(0x1b000)=0x54000; M32(0x54064)=7;
    M8(0x1602c)=1; M32(0x1601c)=0x17000;
    M32(0x12000)=0x55000; M32(0x55070)=8; M32(0x55060)=9; M32(0x55054)=11;
    M32(0x21000)=0x58000; M32(0x58034)=15;
    M32(0x17000)=0x56000; M32(0x56070)=8;
    M32(0x1c000)=0x57000; M32(0x57028)=12;
    M32(0x5701c)=M32(0x57020)=13; M32(0x5700c)=14;
    M8(0x901b28)=1; M32(0x9260c0)=0; M32(0x982ba0)=0;
    M32(0x9885c8)=0; M8(0x90d9d8)=0; M8(0x911055)=0; MF32(0x90d9d4)=0; M32(0x8ee348)=0;
    M32(0x92cea4)=0; run(1,0); expect("skip: no cop in range"); CHECK(!loads); CHECK(strstr(logs,"age 1.000"));
    M32(0x92cea4)=1; run(1,0); expect("PLAYED"); CHECK(loads==1); CHECK(strstr(logs,"cop_distance 10.000 limit 80.000 marker_score 5.000"));
    distance=20; run(1,0); expect("skip: no cop in range"); CHECK(strstr(logs,"limit 20.000")); distance=-10;
    score=75; run(1,0); expect("skip: marker score >= 75"); score=5;
    split=1; run(1,0); expect("skip: splitscreen"); split=0;
    M8(0x41047)=0; run(1,0); expect("skip: event cameras off"); M8(0x41047)=1;
    M8(0x911055)=1; run(1,0); expect("skip: speedbreaker camera"); M8(0x911055)=0;
    pursuit=0; run(1,0); expect("skip: non-pursuit"); pursuit=1;
    now=3; run(1,0); expect("skip: stale request"); now=1;
    missing_player=1; run(1,0); expect("skip: no player"); missing_player=0;
    busted=1; run(1,0); expect("skip: busted"); busted=0;
    missing_sim=1; run(1,0); expect("skip: no simable"); missing_sim=0;
    matching=0; run(1,0); expect("skip: no cop in range"); matching=1;
    active=0; run(1,0); expect("skip: no cop in range"); active=1;
    tracked=0; run(1,0); expect("skip: no cop in range"); tracked=1;
    distance=-80; run(1,0); expect("skip: no cop in range"); distance=-10;
    M32(0x9260c0)=1; run(1,0); expect("skip: animations disabled"); M32(0x9260c0)=0;
    M8(0x901b28)=0; run(1,0); expect("skip: NIS moments disabled"); M8(0x901b28)=1;
    rigid=0; run(1,0); expect("PLAYED"); CHECK(strstr(logs,"limit 80.000")); rigid=1;
    M32(0x9885c8)=1; run(1,0); expect("skip: existing INIS"); M32(0x9885c8)=0;
    factory=0; run(1,0); expect("skip: factory"); factory=1;
    interface=0; run(1,0); expect("skip: no INIS interface"); interface=1;
    allocation=0; run(1,0); expect("skip: scene allocation"); allocation=1;
    M32(0x9260c0)=1; lines=0;
    Cpu standalone={0}; standalone.esp=0x780000; standalone.ecx=0x1c000; standalone.cw=0x37f;
    M32(standalone.esp+4)=4; M32(standalone.esp+8)=0x30000;
    breaker_diag_load(&standalone); expect("skip: animations disabled"); M32(0x9260c0)=0;
    // Off A/B compares all Cpu fields and guest memory, with and without cops.
    for(int n=0;n<2;++n) {
        M32(0x92cea4)=n;
        Cpu a=run(0,0); CHECK(!lines && !observes); unsigned la=loads;
        uint8_t *before=malloc(0x1000000); memcpy(before,G,0x1000000);
        Cpu b=run(0,1); CHECK(!lines && !observes && loads==la);
        CHECK(!memcmp(&a,&b,sizeof a)); CHECK(!memcmp(before,G,0x1000000)); free(before);
        Cpu on=run(1,0); CHECK(lines==1 && observes>0);
        before=malloc(0x1000000); memcpy(before,G,0x1000000);
        Cpu off=run(0,0); CHECK(!lines && !observes);
        CHECK(!memcmp(&on,&off,sizeof on)); CHECK(!memcmp(before,G,0x1000000)); free(before);
    }
    printf("breaker checks=%u failures=0; on/off + original A/B Cpu + 16 MiB identical (0/1 cops)\n",checks);
    return 0;
}
'''

def main():
    if not Path(recomp.EXE).is_file():
        print('SKIP: private translated executable absent'); return 3
    rc = recomp.Recompiler(pe=recomp.PE()); rc.calls = True
    starts = sorted(int(l.split('\t')[0],16) for l in (ROOT/'recomp/functions_v13.tsv').read_text().splitlines() if re.match(r'^[0-9a-fA-F]{8}(?:\t|$)',l))
    rc.functions = dict(zip(starts,starts[1:]))
    bodies=[]
    for addr in (0x640f00,0x6f51f0,0x410120,0x410070,0x40fe20):
        code,error=rc.translate(addr,rc.functions[addr]); assert not error,error
        bodies.append((addr,'',code))
    stubs={
        0x5c5e80:"x87_push(c,sqrtf(argf(c,0))); c->esp+=4;",
        0x46e2d0:"c->esp+=12;",
        0x5cb5d0:"x87_push(c,0); c->esp+=4;",
        0x6f3560:"c->esp+=16;",
        0x446f00:"c->esp+=4;",
        0x629a90:'c->eax=arg(c,1); c->esp+=4;',
        0x460c50:'c->eax=strlen(gptr(arg(c,0))); c->esp+=4;',
        0x460dd0:'c->eax=strcasecmp(gptr(arg(c,0)),gptr(arg(c,1))); c->esp+=4;',
        0x469120:'c->eax=split; c->esp+=4;',
        0x5fbe70:'c->eax=pursuit; c->esp+=4;',
        0x6e8de0:'x87_push(c,now); c->esp+=4;',
        0x425370:'c->eax=missing_player?0:0x11000; c->esp+=4;',
        0x40e020:'M32(arg(c,0))=0x1b000; c->eax=1; c->esp+=8;',
        0x47e690:'c->esp+=12;',
        0x5cc240:'c->eax=123; c->esp+=4;',
        0x41cb50:'c->eax=factory?0x1d000:0; c->esp+=4;',
        0x5d59f0:'c->eax=interface?0x1c000:0; c->esp+=8;',
        0x460bf0:'c->eax=42; c->esp+=4;',
        0x460e30:'c->eax=1; c->esp+=4;',
        0x7c4b80:'c->eax=0; x87_pop(c); c->esp+=4;',
        0x4491e0:'MF32(arg(c,3))=score; c->eax=0x1e000; c->esp+=24;',
        0x6ea390:'c->esp+=12;',
        0x477e10:'c->esp+=16;',
        0x45ecf0:'c->esp+=4;',
        0x47ef70:'c->eax=0; c->esp+=12;',
        0x627400:'c->eax=0; c->esp+=4;',
        0x652ac0:'c->eax=allocation?0x1f000:0; c->esp+=4;',
        0x6ef2c0:'c->eax=0x1f000; c->esp+=20;',
    }
    called={int(x,16) for _,_,code in bodies for x in re.findall(r'f_([0-9A-F]{8})\(c\)',code)}
    with tempfile.TemporaryDirectory(prefix='breaker-diag-') as d:
        tmp=Path(d); memory=bytearray(0x1000000)
        for _,vs,rva,rs,_ in rc.pe.sections:
            if rva>=0x490000:
                data=rc.pe.read(rc.pe.base+rva,min(vs,rs))
                if data: memory[rc.pe.base+rva:rc.pe.base+rva+len(data)]=data
        (tmp/'memory.bin').write_bytes(memory)
        copies=build_all.breaker_diagnostic_bodies(bodies)
        for addr,_,body in bodies[:2]:
            clone=re.search(r'static void breaker_%08X\(.*?\n\}' % addr,copies,re.S)[0]
            clean=re.sub(r'    breaker_observe\(c, 0x[0-9A-F]{8}\);\n','',clone)
            assert clean.replace(f'static void breaker_{addr:08X}',f'void f_{addr:08X}',1)==body
        (tmp/'breaker_diag.inc').write_text(copies)
        source=(ROOT/'runtime/breaker_diag.c').read_text().replace('BreakerDecision *d = decision;', '++observes; BreakerDecision *d = decision;')
        (tmp/'breaker_diag.c').write_text(source)
        # Compile the actual production wrapper, including its delegation choice.
        hook=re.search(r'^void f_006F51F0\(Cpu \*c\) \{.*?^\}',
                       (ROOT/'runtime/diag.c').read_text(),re.M|re.S)[0]
        (tmp/'breaker_load_hook.inc').write_text(hook)
        translated='\n'.join(code.replace(f'void f_{a:08X}(',f'void f_{a:08X}_orig(',1) if a in (0x640f00,0x6f51f0) else code for a,_,code in bodies)
        stubtext='\n'.join(f'void f_{a:08X}(Cpu *c) {{ {stubs.get(a,f"unexpected(0x{a:X});")} }}' for a in sorted(called-{a for a,_,_ in bodies}-{0x6f51f0}))
        harness=HARNESS.replace('STUBS',stubtext).replace('TRANSLATED',translated)
        (tmp/'t.c').write_text(harness)
        def run(tag):
            subprocess.run(['clang','-O1','-I'+str(tmp),'-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),str(tmp/'t.c'),'-o',str(tmp/'t')],check=True,capture_output=True)
            result=subprocess.run([str(tmp/'t'),str(tmp/'memory.bin')],capture_output=True,text=True)
            print(tag,result.returncode,result.stdout.strip(),result.stderr.strip()); return result.returncode
        if run('normal'): return 1
        assert hook.count('breaker_diag_load(c);')==1
        (tmp/'breaker_load_hook.inc').write_text(hook.replace('breaker_diag_load(c);', 'f_006F51F0_orig(c);'))
        if run('Load-hook reversion mutant')==0: return 1
        (tmp/'breaker_load_hook.inc').write_text(hook)
        (tmp/'breaker_diag.c').write_text(source.replace('if (!diag_on) { f_00640F00_orig(c);', 'if (1) { f_00640F00_orig(c);'))
        if run('disabled-observer mutant')==0: return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
