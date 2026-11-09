#!/usr/bin/env python3
"""Translated Junkman stock, availability, cart and save regression.

Runs production hooks and complete translated functions with ASan/UBSan and
translated ABI checks. Stubs capture UI output, draw heat, supply the induction
label, control vehicle type/installed bits, and reject unvisited external calls.
Production: 10,643 checks / 0 failures; finite-stock control: 1,211 failures;
mixed-header control: 49 failures.
Covers all seven types and both completion states, empty/duplicate/full
inventories, mixed carts, BMW entry, warning acknowledgement, 756 save bytes,
profile bytes and ABI sentinels. The finite-stock and mixed-header controls
must fail. Shop construction is stubbed; checkout, package installation and
shop exit execute their complete translated bodies. A completed career keeps
virtual stock through repeated visits and save reloads.
UI policy: show real unspent stock, floored at 1, so empty types stay visible;
Junkman costs are 0 and cosmetic costs never reduce that displayed stock.
Game check pending: apply parts to two cars, inspect queued/cart counts and
costs and save/reload; unbeaten careers retain finite stock.
The game tool starts fresh scratch userdata and provides no beaten-career
seed/load mechanism; no game runs were used. Autodiscovered by offline_tests.sh.
No game, GPU, desktop, save top-ups or emulation.
"""
from pathlib import Path
import re
import os
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HOOKS = '00576E30 0058A680 00576ED0 00576F10 0058AE50 007A6160 007AED80 007B0620 007B13C0'.split()
FUNCTIONS = {
    '21': '00576C90 00576E30 00576ED0 00576F10 00575B00 00575B20 00575B40',
    '22': '0058A680 0058AE50',
    '52': '007BFD30 007BAB60',
    '51': '007AED80 007B01F0 007B0620 007B13C0 007A58D0 007AFDB0 007AEC50 007AEB90 007A57A0 007A4C20 007AEB10',
}
PRE = r'''
#include "funcs.h"
#include "host.h"
#include <stdlib.h>
#include <limits.h>
uint8_t *G;
static uint8_t *image;
static unsigned checks, failures, printed, rowmask;
static int row[8], cost, remaining, itemcost, headerstock;
static unsigned heat_calls, switches, warnings, car_type, installed;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if(printed++<8) printf("FAIL %d: %s\n", __LINE__, #x); } } while(0)
void abi_violation(Cpu *c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",fn); exit(2);
}
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"unexpected %x\n",t); exit(2); }
void dispatch(Cpu *c,uint32_t t) {
 if(t==0x12345678) { c->esp+=8; return; } // cart item's deleting destructor, after unlink
 bad_jump(c,t);
}
void untranslated(Cpu *c,uint32_t t) { bad_jump(c,t); }
#define FE_DATABASE 0x0091CF90u
#define FE_MARKER_MANAGER 0x0091D928u
// Shop construction is an external boundary; retain its control-flag/ABI contract.
// The actual translated RelinquishControl clears the flag at the end of each visit.
void f_007C3690(Cpu *c) { M8(0x9B9E8C)=1; c->esp+=12; }
void f_00652B00(Cpu *c) { CHECK(!arg(c,0)); c->esp+=4; } // release empty temp slots
void f_0056F120(Cpu *c) { c->eax=0x370000; c->esp+=8; } // career record lookup
void f_0056F7B0(Cpu *c) { x87_push(c,1.0); c->esp+=4; } // career record heat
void f_0056F100(Cpu *c) { c->eax=0x360000; c->esp+=8; } // customization record lookup
void f_0056F210(Cpu *c) { c->eax=1; c->esp+=8; } // physics accepts compatible package
void f_007A4BC0(Cpu *c) { c->esp+=12; } // preview rendering
void f_007B5A50(Cpu *c) { c->esp+=4; } // preview reset after checkout
void f_007BB680(Cpu *c) { c->esp+=20; } // parent notification forwards no backroom action
void f_005816B0(Cpu *c) { c->eax=car_type; c->esp+=4; } // controlled vehicle type
void f_00677FE0(Cpu *c) { c->eax=(installed>>arg(c,1))&1; c->esp+=4; } // controlled installed upgrades
void f_007BFC50(Cpu *c) { ++switches; c->esp+=4; } // room-switch boundary
void f_00598A60(Cpu *c) { ++warnings; c->esp+=4; } // warning dialog boundary
void f_007A6160_orig(Cpu *c) {
 unsigned idx=arg(c,0); CHECK(idx>=1&&idx<=7); row[idx]=(int)arg(c,2); rowmask|=1u<<idx; c->esp+=16;
}
void f_005252D0(Cpu *c) {
 uint32_t hash=arg(c,1); int value=(int)arg(c,3);
 if(hash==0xD1497A06) cost=value;
 if(hash==0x18661565) remaining=value;
 if(hash==0x23D918FE) itemcost=arg(c,2)==0x8A04FC?1:value;
 if(hash==0x83E3CD39) headerstock=value;
 c->esp+=4;
}
void f_00525220(Cpu *c) { c->esp+=4; }
void f_00524850(Cpu *c) { c->eax=0x390000; c->esp+=4; }
void f_00514C70(Cpu *c) { c->esp+=4; }
void f_00514CC0(Cpu *c) { c->esp+=4; }
void f_007AFC90(Cpu *c) { ++heat_calls; x87_push(c,1.0); c->esp+=4; }
void f_007AFB60(Cpu *c) { x87_push(c,1.0); c->esp+=8; }
void f_007AFAE0(Cpu *c) { c->eax=1; c->esp+=4; } // induction label only
void f_007A5AA0(Cpu *c) { c->esp+=4; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
 uint32_t sp=c->esp;
 for(int i=n-1;i>=0;--i) { c->esp-=4; M32(c->esp)=args[i]; }
 c->esp-=4; M32(c->esp)=0xDEAD1234;
 switch(fn) {
 case 0x00575B00: f_00575B00(c); break;
 case 0x00575B20: f_00575B20(c); break;
 case 0x005252D0: f_005252D0(c); break;
 default: bad_jump(c,fn);
 }
 c->esp=sp; return c->eax;
}
'''
MAIN = r'''
static const uint32_t mapping[]={6,1,5,7,2,4,3};
static const uint32_t sp=0x800000,db=0x310000,profile=0x320000,screen=0x330000;
static Cpu cpu(uint32_t self) {
 Cpu c={.ecx=self,.esp=sp,.eax=0x76543200,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
 M32(sp)=0xABCD1234; M32(sp+20)=0x1234ABCD; return c;
}
static void abi(Cpu c,unsigned pop) {
 CHECK(c.esp==sp+pop); CHECK(c.ebx==7&&c.esi==8&&c.edi==9&&c.ebp==10);
 CHECK(c.top==0&&c.cw==0x37f); CHECK(M32(sp+20)==0x1234ABCD);
}
static void record(unsigned idx,unsigned type,unsigned payload,unsigned active) {
 uint32_t r=FE_MARKER_MANAGER+idx*12; M32(r)=type; M32(r+4)=payload; M32(r+8)=active;
}
static unsigned raw(unsigned type) {
 unsigned n=0;
 for(unsigned i=0;i<63;i++) {
  uint32_t r=FE_MARKER_MANAGER+i*12;
  n+=M32(r)==type && !M32(r+4) && M32(r+8)==1;
 }
 return n;
}
static void inventory(unsigned kind) {
 memset(G+FE_MARKER_MANAGER,0,756);
 if(kind==1) { // duplicate valid records, inactive and wrong-payload decoys
  for(unsigned t=1;t<=7;t++) { record((t-1)*4,t,0,1); record((t-1)*4+1,t,0,1);
   record((t-1)*4+2,t,0,0); record((t-1)*4+3,t,99,1); }
 } else if(kind==2) for(unsigned i=0;i<63;i++) record(i,1+i%7,0,1);
 else if(kind==3) for(unsigned i=0;i<63;i++) record(i,8+i%8,0,1); // full, no Junkman
}
static void setup(unsigned beaten,unsigned kind) {
 memcpy(G,image,0x1000000);
 M8(0x9B9E8C)=0;
 M32(FE_DATABASE)=db; M32(db+0x10)=profile; M32(profile+0xAC)=0xA1000001|(beaten?0x4000:0);
 M32(db+0x12C)=1;
 M8(0x91D3D0)=1; M8(0x91D3D1)=1; // backroom, performance
 M32(FE_MARKER_MANAGER-4)=0x55667788; M32(FE_MARKER_MANAGER+756)=0x88776655;
 MF32(0x890988)=1.0f;
 for(unsigned i=0;i<7;i++) { M32(0x906194+i*4)=i+1; M32(0x9061B0+i*4)=0; }
 // Display order is marker order; find the corresponding physics type.
 for(unsigned i=0;i<7;i++) for(unsigned t=0;t<7;t++) if(mapping[t]==i+1) M32(0x9061B0+i*4)=t;
 inventory(kind);
 M32(0x9B9EC0+0x1B4)=0x9B9EC0+0x1B4; // empty intrusive cart
 M32(screen+0x10)=0x380000; MF32(screen+0x128)=0; MF32(screen+0x12C)=5;
 M32(screen+4)=0x380000;
}
static void cart(unsigned count,unsigned checked,unsigned cosmetic) {
 const uint32_t manager=0x9B9EC0,base=0x340000,parts=0x350000;
 unsigned n=count+cosmetic;
 M32(manager+0x1B4)=n?base+4:manager+0x1B4;
 for(unsigned i=0;i<n;i++) {
  uint32_t item=base+i*0x40,part=parts+i*0x40;
  M32(item)=0x380100; M32(0x380100)=0x12345678;
  M32(item+4)=i+1<n?item+0x44:manager+0x1B4;
  M32(item+8)=i?item-0x3C:manager+0x1B4;
  M32(item+0xC)=part; M8(item+0x14)=checked;
  M8(part+0x1C)=i<count; M32(part+0x18)=i; M32(part+0x10)=0x17; M32(part+0x14)=7;
 }
 M32(manager+0x1B8)=n?base+(n-1)*0x40+4:manager+0x1B4;
}
int main(int argc,char **argv) {
 G=calloc(1,0x1000000); image=calloc(1,0x1000000); if(!G||!image||argc!=2) return 2;
 FILE *file=fopen(argv[1],"rb"); if(!file||fread(image,1,0x1000000,file)!=0x1000000) return 2; fclose(file);
 for(unsigned beaten=0;beaten<2;beaten++)
 for(unsigned kind=0;kind<4;kind++) for(unsigned type=0;type<7;type++) {
  setup(beaten,kind); int active=beaten; unsigned marker=mapping[type],n=raw(marker);
  uint8_t before[756],expected[756],profile_before[0x100];
  memcpy(before,G+FE_MARKER_MANAGER,756); memcpy(expected,before,756); memcpy(profile_before,G+profile,0x100);
  Cpu c=cpu(FE_MARKER_MANAGER); M32(sp+4)=marker; M32(sp+8)=0; f_00576F10(&c);
  CHECK(c.eax==(active&&!n?1:n)); abi(c,12);
  c=cpu(FE_MARKER_MANAGER); f_00576ED0(&c); CHECK((c.eax&255)==(active||n>0)); abi(c,12);
  c=cpu(0); M32(sp+4)=2; M32(sp+8)=type+4; M32(sp+12)=0; f_0058A680(&c);
  CHECK((c.eax&255)==(active||n>0)); abi(c,4);
  c=cpu(FE_MARKER_MANAGER); f_0058AE50(&c); CHECK(!active||c.eax>=7); abi(c,4);
  // Exact retail inventory mutation: consume the first active, zero-payload match only.
  if(!active) for(unsigned i=0;i<63;i++) {
   uint32_t r=FE_MARKER_MANAGER+12*i;
   if(M32(r)==marker&&!M32(r+4)&&M32(r+8)==1) { memset(expected+12*i,0,4); memset(expected+12*i+8,0,4); break; }
  }
  c=cpu(FE_MARKER_MANAGER); M32(sp+4)=type; f_00576E30(&c); abi(c,8);
  CHECK(!memcmp(expected,G+FE_MARKER_MANAGER,756)); CHECK(!memcmp(profile_before,G+profile,0x100));
  CHECK(M32(FE_MARKER_MANAGER-4)==0x55667788&&M32(FE_MARKER_MANAGER+756)==0x88776655);
  if(active) { // repeated application on another car must leave the same save payload
   c=cpu(FE_MARKER_MANAGER); M32(sp+4)=type; f_00576E30(&c); CHECK(!memcmp(before,G+FE_MARKER_MANAGER,756));
  }
  // Reload the unchanged save bytes; completed careers retain virtual stock.
  uint8_t saved[756]; memcpy(saved,G+FE_MARKER_MANAGER,756); memset(G+FE_MARKER_MANAGER,0,756);
  memcpy(G+FE_MARKER_MANAGER,saved,756);
  c=cpu(FE_MARKER_MANAGER); M32(sp+4)=marker; M32(sp+8)=0; f_00576F10(&c);
  CHECK(c.eax==(active&&!raw(marker)?1:raw(marker)));
 }
 // Cart rows, remaining totals and per-item costs: every stock shape, queued/unchecked, 0..7 types.
 for(unsigned beaten=0;beaten<2;beaten++)
 for(unsigned kind=0;kind<4;kind++) for(unsigned count=0;count<=7;count++) for(unsigned checked=0;checked<2;checked++) {
  setup(beaten,kind); cart(count,checked,0); int active=beaten;
  uint8_t before[756]; memcpy(before,G+FE_MARKER_MANAGER,756);
  cost=remaining=INT_MIN; rowmask=0;
  Cpu c=cpu(screen); f_007B0620(&c); abi(c,4);
  CHECK(rowmask==254); unsigned total=0;
  for(unsigned i=1;i<=7;i++) {
   unsigned stock=raw(i); int debit=0;
   for(unsigned t=0;t<count;t++) if(mapping[t]==i&&checked) debit=1;
   int expected=active?(stock?stock:1):(int)stock-debit;
   CHECK(row[i]==expected); total+=active&&!stock?1:stock;
  }
  CHECK(cost==(active?0:(int)(count*checked))); CHECK(remaining==(int)total-cost);
  for(unsigned type=0;type<7;type++) {
   itemcost=headerstock=INT_MIN; c=cpu(screen); M32(sp+4)=0; M32(sp+8)=0x201+type; M32(sp+12)=0;
   f_007B13C0(&c); abi(c,16); CHECK(itemcost==(active?0:1));
   // Category mapping executes the actual GetNumMarkersFromCategory and marker translation.
   CHECK(!active||headerstock>=1);
  }
  CHECK(!memcmp(before,G+FE_MARKER_MANAGER,756));
 }
 // Mixed cart: only the seven performance items become free, cosmetic markers remain finite.
 for(unsigned mode=0;mode<=2;mode++) for(unsigned backroom=0;backroom<2;backroom++) {
  setup(1,1); cart(7,1,1); M8(0x91D3D0)=backroom;
  Cpu c=cpu(0x9B9EC0); M32(sp+4)=mode; f_007AED80(&c); abi(c,8);
  CHECK(c.eax==(mode==1?0:backroom?1:0));
 }
 // Mixed-cart header with more cosmetic costs than virtual performance stock must keep positive stock.
 for(unsigned kind=0;kind<4;kind++) for(unsigned cosmetic=1;cosmetic<=8;cosmetic++) {
  setup(1,kind); cart(7,1,cosmetic); cost=remaining=INT_MIN;
  Cpu c=cpu(screen); f_007B0620(&c); abi(c,4);
  unsigned total=0; for(unsigned type=1;type<=7;type++) total+=raw(type)?raw(type):1;
  CHECK(cost==(int)cosmetic); CHECK(remaining==(int)total); CHECK(remaining>=7);
 }
 // Actual backroom notification: zero-stock entry, BMW exclusion, turbo/nitrous warning and acknowledgement.
 for(unsigned beaten=0;beaten<2;beaten++)
 for(unsigned kind=0;kind<4;kind++) for(unsigned bmw=0;bmw<2;bmw++) for(unsigned hasparts=0;hasparts<2;hasparts++) {
  setup(beaten,kind); M8(0x91D3D0)=0; car_type=bmw?0x29:0; installed=hasparts?127:0; switches=warnings=0;
  uint8_t before[756]; memcpy(before,G+FE_MARKER_MANAGER,756);
  Cpu c=cpu(screen); M32(sp+4)=0xC519BFC4; M32(sp+8)=M32(sp+12)=M32(sp+16)=0; f_007BFD30(&c); abi(c,20);
  int can_enter=!bmw&&((beaten)||kind>0);
  int warn=can_enter&&!hasparts&&((beaten)||kind==1||kind==2);
  CHECK(warnings==(unsigned)warn); CHECK(switches==(unsigned)(can_enter&&!warn));
  if(warn) {
   c=cpu(screen); M32(sp+4)=0x34DC1BEC; f_007BFD30(&c); abi(c,20);
   CHECK(switches==1); CHECK(M32(screen+0x1AC)==0);
  }
  CHECK(!memcmp(before,G+FE_MARKER_MANAGER,756));
 }
 // Completed career, zero markers: queue, checkout and revisit with unchanged stock.
 for(unsigned beaten=0;beaten<2;beaten++) {
  const uint32_t manager=0x9B9EC0;
  setup(beaten,0); M32(manager+4)=0x380200;
  uint8_t before[756]; memcpy(before,G+FE_MARKER_MANAGER,756);
  Cpu c=cpu(manager); M32(sp+4)=M32(sp+8)=0; f_007C3690(&c); abi(c,12);
  CHECK(M8(0x9B9E8C)==1);
  c=cpu(FE_MARKER_MANAGER); M32(sp+4)=6; M32(sp+8)=0; f_00576ED0(&c);
  int active=beaten; CHECK((c.eax&255)==active);
  cart(active?1:0,1,0); // a checked engine Junkman item, only if actually available
  // Repeated shop entry preserves the built-in policy.
  c=cpu(manager); f_007C3690(&c); abi(c,12);
  c=cpu(FE_MARKER_MANAGER); M32(sp+4)=6; M32(sp+8)=0; f_00576F10(&c); CHECK(c.eax==(unsigned)active);
  cost=remaining=INT_MIN; c=cpu(screen); f_007B0620(&c); abi(c,4);
  CHECK(cost==0); CHECK(remaining==(active?7:0));
  c=cpu(manager); f_007BAB60(&c); abi(c,4);
  CHECK(M32(0x360000+0x134)==(unsigned)active); // translated InstallPerfPkg set the engine bit
  CHECK(M32(manager+0x1B4)==manager+0x1B4); CHECK(!memcmp(before,G+FE_MARKER_MANAGER,756));
  c=cpu(manager); f_007AEB10(&c); abi(c,4); CHECK(!M8(0x9B9E8C));
  c=cpu(manager); f_007C3690(&c); abi(c,12);
  c=cpu(FE_MARKER_MANAGER); M32(sp+4)=6; M32(sp+8)=0; f_00576F10(&c);
  CHECK(c.eax==beaten);
 }
 // Non-Junkman records/payloads and absent databases retain the original answers.
 for(unsigned type=0;type<=16;type++) for(unsigned payload=0;payload<2;payload++) {
  setup(1,3); Cpu actual=cpu(FE_MARKER_MANAGER),original=actual;
  M32(sp+4)=type; M32(sp+8)=payload; f_00576F10(&actual); f_00576F10_orig(&original);
  if(type<1||type>7||payload) CHECK(actual.eax==original.eax);
 }
 for(unsigned nullcase=0;nullcase<2;nullcase++) {
  setup(1,0); if(nullcase) M32(db+0x10)=0; else M32(FE_DATABASE)=0;
  Cpu c=cpu(FE_MARKER_MANAGER); M32(sp+4)=1; M32(sp+8)=0; f_00576F10(&c); CHECK(c.eax==0);
 }
 printf("%u checks, %u failures\n",checks,failures); free(G); free(image); return failures?1:0;
}
'''



def main():
    if not (ROOT/'recomp/out/speed_51.c').exists():
        print('SKIP: translated PC functions require a local build')
        raise SystemExit(3)
    exe = Path(os.environ.get('NFS_SPEED_EXE') or Path(os.environ.get('NFS_GAME', ROOT.parent/'Need For Speed Most Wanted Black Edition'))/'speed.exe')
    if not exe.exists():
        print('SKIP: retail jump tables require the local PC executable')
        raise SystemExit(3)
    data = exe.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    sections = struct.unpack_from('<H', data, pe+6)[0]
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    base = struct.unpack_from('<I', data, pe+24+28)[0]
    image = bytearray(0x1000000)
    for i in range(sections):
        header = pe+24+optional_size+40*i
        va, size, offset = struct.unpack_from('<III', data, header+12)
        image[base+va:base+va+size] = data[offset:offset+size]
    bodies = []
    for chunk, names in FUNCTIONS.items():
        source = (ROOT/f'recomp/out/speed_{chunk}.c').read_text()
        for fn in names.split():
            match = re.search(r'void f_'+fn+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', source, re.S)
            assert match, fn
            body = match.group()
            body = re.sub(r'^void f_'+fn+r'(?:_orig)?', 'void f_'+fn+('_orig' if fn in HOOKS else ''), body)
            bodies.append(body)
    source = (ROOT/'runtime/gameplay_opts.c').read_text()
    hooks = (ROOT/'recomp/hooks.txt').read_text()
    for fn in HOOKS:
        assert len(re.findall(r'^'+fn+r'\s', hooks, re.M)) == 1, f'unregistered hook: {fn}'
    production = '// Junkman stock is virtual only'+source.split('// Junkman stock is virtual only', 1)[1]
    used = set(re.findall(r'\bf_[0-9A-F]{8}(?:_orig)?(?=\()', '\n'.join(bodies)+production))
    defined = set(re.findall(r'void (f_[0-9A-F]{8}(?:_orig)?)\(Cpu \*', PRE+'\n'+'\n'.join(bodies)+production))
    stubs = '\n'.join(f'void {fn}(Cpu *c) {{ bad_jump(c,0x{fn[2:10]}u); }}' for fn in sorted(used-defined))
    declarations = '\n'.join(f'void f_{fn}_orig(Cpu *c);' for fn in HOOKS)
    with tempfile.TemporaryDirectory(prefix='junkman-') as directory:
        tmp = Path(directory)
        image_path = tmp/'image.bin'
        image_path.write_bytes(image)
        for variant in ('retail-control', 'mixed-header-control', 'actual'):
            policy = production
            if variant == 'retail-control':
                policy, n = re.subn(r'return profile && \(M32\(profile \+ 0xAC\) & 0x4000u\);', 'return 0;', policy)
                assert n == 1
            elif variant == 'mixed-header-control':
                original = 'f_007B0620_orig(c);\n    if (active) {'
                assert policy.count(original) == 1
                policy = policy.replace(original, 'f_007B0620_orig(c);\n    if (0) {')
            path, exe = tmp/f'{variant}.c', tmp/variant
            path.write_text(PRE+declarations+stubs+'\n'+'\n'.join(bodies)+'\n'+policy+'\n'+MAIN)
            command = ['clang', '-std=c11', '-g', '-O1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                       '-DRECOMP_ABICHECK', '-I', str(ROOT/'recomp'), '-I', str(ROOT/'recomp/out'),
                       '-I', str(ROOT/'runtime'), str(path), '-o', str(exe)]
            build = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            assert build.returncode == 0, build.stdout[-5000:]
            run = subprocess.run([str(exe), str(image_path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            result = run.stdout.splitlines()[-1] if run.stdout else '(no output)'
            assert run.returncode == (0 if variant == 'actual' else 1), (variant, run.returncode, run.stdout[-5000:])
            assert re.fullmatch(r'\d+ checks, \d+ failures', result), run.stdout[-3000:]
            print(f'{variant}: {result}')
    print('junkman_test: PASS (retail and mixed-header controls rejected)')


if __name__ == '__main__':
    main()
