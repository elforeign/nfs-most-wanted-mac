#!/usr/bin/env python3
"""















"""
from pathlib import Path
import os
import re
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def translated(file, name, original=False):
    source = (ROOT / 'recomp/out' / file).read_text()
    body = re.search(r'void f_' + name + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', source, re.S).group()
    return re.sub(r'void f_' + name + r'(?:_orig)?\(',
                  'void f_' + name + ('_orig' if original else '') + '(', body, count=1)


PRE = r'''
#include "funcs.h"
#include "host.h"
#include "settings.h"
#include <stdlib.h>
#include <stdarg.h>
// Split-menu cases have no saved LAN setting; lan_toggle_test covers the registry.
Setting *settings_find(const char *id) { (void)id; return NULL; }
uint8_t *G;
int diag_on;
static int checks, failures, added, allocations, fail_third, special_build;
static unsigned finalizations, switches, ride_refreshes;
static uint32_t options[4], textures[4], last_package;
#define MENU 0x300000u
#define DB 0x310000u
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)
static Cpu cpu(uint32_t self) {
 return (Cpu){.ecx=self,.esp=0x800000,.fs_base=0x700000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
}
static Cpu invoke(void (*fn)(Cpu *),uint32_t self,unsigned n,const uint32_t *args) {
 Cpu c=cpu(self);
 for(unsigned i=0;i<n;i++) M32(c.esp+4+4*i)=args[i];
 fn(&c);
 CHECK(c.esp==0x800004+4*n); CHECK(c.ebx==7); CHECK(c.esi==8);
 CHECK(c.edi==9); CHECK(c.ebp==10); CHECK(c.top==0 && c.cw==0x37f);
 return c;
}
void diag_event(const char *fmt,...) { (void)fmt; }
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"jump %x\n",t); exit(2); }
void abi_violation(Cpu *c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",fn); exit(2);
}
void f_0056E2B0(Cpu *c) { c->eax=special_build; c->esp+=4; }
void f_00652AD0(Cpu *c) {
 CHECK(arg(c,0)==0x4c); ++allocations;
 c->eax=fail_third && allocations==3 ? 0 : 0x400010+0x100*(allocations-1);
 if(c->eax) memset(G+c->eax-16,0xa5,0x4c+32);
 c->esp+=4; // cdecl
}
void f_00515B90(Cpu *c) {
 unsigned i=arg(c,0)-0x500000; CHECK(i<4); textures[i]=arg(c,1); c->esp+=4;
}
void f_005AF1C0(Cpu *c) { CHECK(arg(c,0)==0x320000); c->eax=0x1234; c->esp+=4; }
void f_00571E00(Cpu *c) { CHECK(c->ebx==0x1234); c->eax=0x330000; c->esp+=4; }
void f_005A3B60(Cpu *c) { CHECK(c->ecx==DB); ++ride_refreshes; c->esp+=4; }
void f_00587D30(Cpu *c) {
 // Completion is emitted after the real scroller Accept reaction. This sink
 // cannot set QRMode, the DB flags or its mode byte; those bodies run below.
 CHECK(arg(c,0)==0xe1fde1d1); c->esp+=20;
}
void f_00525940(Cpu *c) {
 ++switches; last_package=arg(c,0);
 CHECK(arg(c,1)==0 && arg(c,2)==0 && arg(c,3)==0); c->esp+=20;
}
void dispatch(Cpu *c,uint32_t t) {
 if(t==0x101) {
  CHECK(c->ecx==MENU+0x2c); CHECK(added<4);
  options[added]=arg(c,0); CHECK(options[added]!=0);
  c->eax=0x500000+added++; c->esp+=8; return;
 }
 if(t==0x102) {
  CHECK(c->ecx==MENU+0x2c); CHECK(arg(c,0)==1);
  CHECK(added==(special_build?1:2)+(pad_count()==2 && !(fail_third && !special_build)));
  ++finalizations; c->esp+=8; return;
 }
 if(t==0x007AA6C0) { f_007AA6C0(c); return; }
 if(t==0x007AA710) { f_007AA710(c); return; }
 fprintf(stderr,"dispatch %x\n",t); exit(2);
}
static void reset(void) {
 memset(G,0,0x1000000); memset(options,0,sizeof options); memset(textures,0,sizeof textures);
 added=allocations=0; finalizations=switches=ride_refreshes=0; last_package=0;
 M32(0x91cf90)=DB; M32(MENU+0x2c)=0x340000;
 M32(0x340008)=0x101; M32(0x340040)=0x102;
 M32(MENU+0x10)=0x320000; M8(0x330004)=1; M8(MENU+0x129)=1;
 // Retail PE vtable pairs are checked by the Python driver before compilation.
 M32(0x8b7588)=0x7b4950; M32(0x8b758c)=0x7aa6c0;
 M32(0x8b7590)=0x7b4980; M32(0x8b7594)=0x7aa710;
 M32(0x9b9e68)=99;
}
static void snapshot(FILE *f,Cpu c) { fwrite(G,1,0x1000000,f); fwrite(&c,1,sizeof c,f); }
static void bounds(void) {
 for(int i=0;i<allocations;i++) {
  if(fail_third && i==2) continue;
  uint32_t p=0x400010+0x100*i;
  for(unsigned j=0;j<16;j++) { CHECK(M8(p-16+j)==0xa5); CHECK(M8(p+0x4c+j)==0xa5); }
 }
}
static void react(unsigned i,uint32_t event) {
 uint32_t args[]={0x320000,event,0,0,0};
 invoke(dispatch_react,options[i],5,args);
}
'''

# Declared before PRE's react helper; dispatched through the actual option vtable.
REACT = r'''
static void dispatch_react(Cpu *c) { dispatch(c,M32(M32(c->ecx)+4)); }
'''

MAIN = r'''
static unsigned predicate(void) { return invoke(f_00469120,DB,0,NULL).eax; }
static void complete(void) {
 M32(MENU+0x138)=0xc407210;
 uint32_t args[]={0xe1fde1d1,0,0,0}; invoke(f_007A2E20,MENU,4,args);
}
int main(int argc,char **argv) {
 if(argc!=2) return 2;
 G=calloc(1,0x1000000); FILE *out=fopen(argv[1],"wb"); if(!G || !out) return 2;
 unsigned two=pad_count()==2;
 for(unsigned selection=0;selection<2+two;selection++) {
  reset();
  Cpu c=invoke(f_007AA730,MENU,0,NULL);
  CHECK(added==(int)(2+two)); CHECK(allocations==added);
  CHECK(finalizations==1 && ride_refreshes==1 && switches==0);
  CHECK(textures[0]==0xe6313967 && textures[1]==0x2a49b5e2);
  CHECK(M32(options[0]+0x2c)==0xb5e8f82f && M32(options[1]+0x2c)==0x25bbd4c3);
  CHECK(M32(options[0])==0x8b7588 && M32(options[1])==0x8b7590);
  if(two) {
   CHECK(textures[2]==0xf365b5f5); CHECK(M32(options[2]+0x2c)==0x841d518a);
   CHECK(M32(options[2])==0x8b7590); CHECK(M8(options[2]+0x44)==1);
  }
  bounds(); snapshot(out,c);
  // Missing-entry mutants must fail the count/hash checks without dispatching
  // a null option; keep their failure an assertion result, not a crash.
  if(!options[selection]) continue;
  // The real top-level Quick Race reaction, rather than a synthetic flags=4.
  uint32_t args[]={0x320000,0xc407210,0,0,0};
  c=invoke(f_0051AD20,0x350000,5,args);
  CHECK(M32(DB+0x12c)==4); CHECK(predicate()==0);
  react(selection,0x911ab364); CHECK(M32(0x9b9e68)==99);
  react(selection,0xc407210); CHECK(M32(0x9b9e68)==selection);
  CHECK(M8(DB)==0 && M32(DB+0x12c)==4); snapshot(out,c);
  complete();
  CHECK(switches==1);
  CHECK(last_package==(selection==0?0x89fc4c:0x89d1f0));
  CHECK(M8(DB)==(selection==2?2:1));
  CHECK(M32(DB+0x12c)==(selection==0?4:0x404));
  CHECK(predicate()==(selection==2)); snapshot(out,c);
  // 0x400 alone does not make split mode, even after a split reaction.
  M32(DB+0x12c)&=~4u; CHECK(predicate()==0);
 }
 // Unrelated last-button callers must not grow a menu or borrow ESI as one.
 reset(); Cpu c=cpu(0); c.esi=0; M32(c.esp)=0x123456; M32(c.esp+4)=0x320000;
 f_00571E80(&c); CHECK(allocations==0 && c.esi==0 && c.esp==0x800004);
 snapshot(out,c);
 // Matching just one of the split hashes must retain Custom Race's reaction.
 reset();
 for(unsigned mismatch=0;mismatch<2;mismatch++) {
  M32(0x36000c)=mismatch?0xf365b5f5:0x2a49b5e2;
  M32(0x36002c)=mismatch?0x25bbd4c3:0x841d518a;
  uint32_t args[]={0x320000,0xc407210,0,0,0};
  c=invoke(f_007AA710,0x360000,5,args); CHECK(M32(0x9b9e68)==1);
 }
 snapshot(out,c);
 // Failed extra allocation keeps the original two options and finalization.
 reset(); fail_third=1; c=invoke(f_007AA730,MENU,0,NULL);
 CHECK(added==2); CHECK(finalizations==1 && ride_refreshes==1); bounds(); snapshot(out,c);
 fail_third=0;
 fclose(out); free(G);
 printf("qrsplit NFS_PADS=%s: %d checks, %d failures\n",getenv("NFS_PADS")?:"unset",checks,failures);
 return failures!=0;
}
'''


def run(args, **kw):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    if p.returncode:
        raise RuntimeError(p.stdout[-6000:])
    return p.stdout


def verify_assets():
    game = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    data = (game / 'speed.exe').read_bytes()
    pe, = struct.unpack_from('<I', data, 0x3c)
    n, = struct.unpack_from('<H', data, pe + 6)
    osz, = struct.unpack_from('<H', data, pe + 20)
    base, = struct.unpack_from('<I', data, pe + 52)
    for address, expected in [(0x8b7588, (0x7b4950, 0x7aa6c0)), (0x8b7590, (0x7b4980, 0x7aa710))]:
        for i in range(n):
            _, _, va, size, offset = struct.unpack_from('<8sIIII', data, pe + 24 + osz + 40*i)
            if va <= address-base < va+size:
                assert struct.unpack_from('<II', data, offset+address-base-va) == expected
                break
        else:
            raise AssertionError(f'missing vtable {address:x}')
    language = (game / 'LANGUAGES/English.bin').read_bytes()
    record = language.index(bytes.fromhex('8a511d84'))
    offset, = struct.unpack_from('<I', language, record+4)
    # Language chunk: header offset +8, string-area offset at file +0x14.
    string_base = struct.unpack_from('<I', language, 0x14)[0]+8
    caption = language[string_base+offset:].split(b'\0')[0]
    assert caption == b'Split Screen', caption
    assert bytes.fromhex('f5b565f3') in (game / 'FRONTEND/FrontB.lzc').read_bytes()
    print(f'PC assets/vtables: PASS; English record {record:#x} -> {caption.decode()}')


def main():
    verify_assets()
    hook = (ROOT / 'runtime/qrsplit.c').read_text()
    pad = re.search(r'int pad_count\(void\) \{.*?\n\}', (ROOT / 'runtime/user32.m').read_text(), re.S).group()
    console = (ROOT / 'runtime/fe_console.c').read_text()
    console = console[console.index('// MainLAN follows the original menu/login'):console.index('// Options > Audio')]
    groups = [('speed_07.c', ['00469120']), ('speed_17.c', ['0051AD20']),
              ('speed_21.c', ['00573960', '00586FA0', '00571E80']),
              ('speed_51.c', ['007AA730', '007AA6E0', '007AA6C0', '007AA710', '007A2E20'])]
    bodies = '\n'.join(translated(file, name, name in ('00573960', '00571E80', '007AA710'))
                       for file, names in groups for name in names)
    baseline = hook
    for name in ('00571E80', '007AA710'):
        baseline, count = re.subn(r'void f_'+name+r'\(Cpu \*c\) \{.*?\n\}',
                                 'void f_'+name+'(Cpu *c) { f_'+name+'_orig(c); }', baseline, count=1, flags=re.S)
        assert count == 1
    mutants = {f'reaction-mode-{mode}': hook.replace('M32(QS_MODE) = 2;', f'M32(QS_MODE) = {mode};')
               for mode in (0, 1)}
    mutants['entry-missing'] = hook.replace('pad_count() == 2 && M32(c->esp)', 'pad_count() == 99 && M32(c->esp)')
    with tempfile.TemporaryDirectory(prefix='qrsplit-') as directory:
        tmp = Path(directory)
        def compile_one(name, source):
            path = tmp / (name+'.c')
            prefix = PRE.replace('static void react(', REACT+'\nstatic void react(')
            path.write_text(prefix+'\n'+pad+'\n'+console+'\n'+bodies+'\n'+source+'\n'+MAIN)
            exe = tmp / name
            run(['clang', '-std=c11', '-g', '-O1', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-DRECOMP_ABICHECK',
                 '-I', str(ROOT/'recomp'), '-I', str(ROOT/'recomp/out'), '-I', str(ROOT/'runtime'),
                 str(path), '-o', str(exe)])
            return exe
        exe, old = compile_one('actual', hook), compile_one('baseline', baseline)
        for mode in (None, '1', 'invalid', '2'):
            env = dict(os.environ)
            env.pop('NFS_PADS', None)
            env.pop('ASAN_OPTIONS', None)
            if mode is not None:
                env['NFS_PADS'] = mode
            sample, original = tmp/'actual.bin', tmp/'original.bin'
            print(run([str(exe), str(sample)], env=env).strip())
            if mode != '2':
                run([str(old), str(original)], env=env)
                assert sample.read_bytes() == original.read_bytes(), f'{mode}: guest memory/CPU differ'
                print(f'  {mode or "unset"}: {sample.stat().st_size:,} guest/CPU bytes identical to original bodies')
        for name, source in mutants.items():
            assert source != hook
            broken = compile_one(name, source)
            p = subprocess.run([str(broken), str(tmp/'mutant.bin')], env=dict(os.environ, NFS_PADS='2'),
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            assert p.returncode == 1 and 'checks,' in p.stdout, (name, p.stdout[-2000:])
            print('  rejected '+name+': '+p.stdout.splitlines()[-1])
    print('qrsplit_test: PASS')


if __name__ == '__main__':
    main()
