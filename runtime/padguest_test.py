#!/usr/bin/env python3
"""











"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BASELINE = 'c01984d1d71fd480d18e4f36cfb195a82a5b8528'


def translated(file, name):
    source = (ROOT / 'recomp/out' / file).read_text()
    # Also works before rebuilding after the hook registration is added.
    pat = r'void f_' + name + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}'
    body = re.search(pat, source, re.S).group()
    if name in ('00634B50', '006349B0'):
        body = re.sub(r'void f_' + name + r'(?:_orig)?\(', 'void f_' + name + '_orig(', body, count=1)
    return body


PRE = r'''
#include "funcs.h"
#include "host.h"
#include "settings.h"
#include <stdio.h>
#include <stdlib.h>
uint8_t *G;
int diag_on;
static int checks, failures, backend_inits, polls, key_reads, mouse_reads, follows, swaps;
static int physical_reads[2], capture_calls;
static uint32_t d[2] = {0x310000, 0x320000};
static uint8_t fake_pad[2][0x224];
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
void settings_add(Setting *s) { (void)s; }
void diag_event(const char *fmt, ...) { (void)fmt; }
int padbind_port_begin(int idx) { (void)idx; return 0; }
void padbind_port_end(int swapped) { (void)swapped; }
void padbind_follow(Cpu *c) { (void)c; ++follows; }
void padbind_swap(int on) { swaps += on ? 1 : -1; CHECK(swaps >= 0 && swaps <= 1); }
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"bad jump %x\n",t); exit(2); }
void abi_violation(Cpu *c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
    (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",fn); exit(2);
}
void f_005D8660(Cpu *c) { c->esp+=8; }
void f_005D7930(Cpu *c) { c->esp+=12; }
void f_006278F0(Cpu *c) { c->eax=c->ecx; c->esp+=8; }
void f_006282E0(Cpu *c) { c->esp+=4; }
void f_00652AD0(Cpu *c) { c->eax=0x600000; c->esp+=4; }
void f_007F0FE0(Cpu *c) { memset(G+arg(c,0),arg(c,1),arg(c,2)); c->esp+=4; }
void f_007F0E0F(Cpu *c) { memcpy(G+arg(c,0),G+arg(c,1),arg(c,2)); c->esp+=4; }
void f_00634900(Cpu *c) { ++capture_calls; c->esp+=4; }
void f_00628410(Cpu *c) {
    ++backend_inits;
    // Callback 006283D0 keeps keyboard/mouse separate, appends pad objects in DI order.
    M32(0x91f1d8)=0x100000; M32(0x100000)=0x110000; M32(0x11001c)=11;
    M32(0x91f1e0)=0x100100; M32(0x100100)=0x110100; M32(0x110104)=12;
    M32(0x9203b4)=0x100200; M32(0x100200)=0x110200;
    M32(0x110208)=13; M32(0x110218)=14;
    for(int i=0;i<2;i++) {
        uint32_t obj=0x101000+0x100*i, vt=0x111000+0x100*i;
        M32(0x91f150+4*i)=obj; M32(obj)=vt; M32(vt+4)=15+i;
    }
    c->esp+=4;
}
void dispatch(Cpu *c,uint32_t t) {
    switch(t) {
    case 11: ++key_reads; c->eax=1; c->esp+=8; break;
    case 12: ++mouse_reads; c->eax=0x120000; c->esp+=4; break;
    case 13:
        ++polls;
        for(int i=0;i<2;i++) memcpy(G+0x121000+0x1000*i,fake_pad[i],0x224);
        c->esp+=4; break;
    case 14: c->eax=0; c->esp+=4; break;
    case 15: case 16:
        ++physical_reads[t-15]; c->eax=0x121000+0x1000*(t-15); c->esp+=4; break;
    default: fprintf(stderr,"unexpected dispatch %x\n",t); exit(2);
    }
}
static Cpu cpu(uint32_t self) {
    return (Cpu){.ecx=self,.esp=0x800000,.fs_base=0x700000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
}
static void abi(Cpu *c,int args) {
    CHECK(c->esp==0x800004u+4u*args); CHECK(c->ebx==7); CHECK(c->esi==8); CHECK(c->edi==9); CHECK(c->ebp==10);
}
static void bind(int a,int b,int type,int slot,int obj) {
    uint32_t r=0x91f41c+52*a+12*b; M32(r)=type; M32(r+4)=slot; M32(r+8)=obj;
}
static void put(int p,int off,uint32_t v) { memcpy(fake_pad[p]+off,&v,4); }
static void fake_frame(int frame) {
    for(int p=0;p<2;p++) {
        memset(fake_pad[p],0,0x224);
        for(int a=0;a<3;a++) { put(p,4*a,32768); put(p,0x20c+4*a,32768); }
        for(int a=0;a<2;a++) put(p,0x218+4*a,32768);
        put(p,0x220,0xffffffff);
        put(p,0xc,(frame+p)%2 ? 128 : 0);
        put(p,0x10,p ? 32 : 96);
        put(p,0,p ? (frame%2 ? 65535 : 0) : 49151);
        put(p,8,p ? 60000 : 40000);
        put(p,0x214,p ? 45000 : 50000);
        put(p,0x210,p ? 20000 : 55000);
        put(p,0x220,(frame%8)*4500);
    }
    M32(0x120008)=77; M8(0x12000c)=M8(0x12000d)=M8(0x12000e)=1;
}
static float value(int p,int action) { return MF32(M32(d[p]+0x20)+4*action); }
static float eval(int action,int binding,int slot) {
    uint32_t r=0x91f41c+52*action+12*binding, saved=M32(r+4);
    M32(r+4)=slot; Cpu q=cpu(0); M32(q.esp+4)=action; M32(q.esp+8)=binding;
    f_00628940(&q); M32(r+4)=saved; return (float)q.st[q.top];
}
static void sample(FILE *out,Cpu *c) {
    fwrite(G+d[0],1,0x1858,out); fwrite(G+d[1],1,0x1858,out);
    fwrite(G+0x91f148,1,0x100,out); fwrite(G+0x91f418,1,76*52,out);
    fwrite(c,1,sizeof *c,out);
    int counts[]={polls,backend_inits,key_reads,mouse_reads,follows,swaps,capture_calls,physical_reads[0],physical_reads[1]};
    fwrite(counts,1,sizeof counts,out);
}
'''

MAIN = r'''
int main(int argc,char **argv) {
    if(argc!=2) return 2;
    FILE *out=fopen(argv[1],"wb"); if(!out) return 2;
    char path[4096]; snprintf(path,sizeof path,"%s.p0",argv[1]);
    FILE *p0=fopen(path,"wb"); if(!p0) return 2;
    G=calloc(1,0x1000000); if(!G) return 2;
    MF32(0x890968)=0; MF32(0x89096c)=1; MF32(0x8a7a58)=32767.5f;
    MF32(0x8a7a5c)=.0078125f; MF32(0x8957bc)=3.0518044e-05f;
    M32(0x91ddbc)=0xabcdef01;
    for(int p=0;p<2;p++) {
        memset(G+d[p]-16,0xa5,0x1858+32);
        Cpu c=cpu(d[p]); M32(c.esp+4)=p; f_00634B50(&c); abi(&c,1);
        CHECK(c.eax==d[p]); CHECK(M32(d[p]+0x24)==(uint32_t)p);
        CHECK(M32(d[p]-4)==0xa5a5a5a5 && M32(d[p]+0x1858)==0xa5a5a5a5);
        if(!p || pad_count()==2) {
            CHECK(M32(d[p]+0x18)==d[p]+0x34);
            CHECK(M32(d[p]+0x1c)==d[p]+0x1034);
            CHECK(M32(d[p]+0x20)==d[p]+0x1434);
            if(M32(d[p]+0x20)!=d[p]+0x1434 || M32(d[p]+0x1c)!=d[p]+0x1034) goto done;
            for(int i=0;i<0x800;i++) CHECK(M8(d[p]+0x1034+i)==0);
            for(int i=0;i<256;i++) {
                uint32_t r=M32(d[p]+0x18)+16*i;
                CHECK(M32(r)==0 && M32(r+4)==0xabcdef01 && M32(r+8)==0 && M32(r+12)==0);
            }
            CHECK(M8(d[p]+0x183c)==0); CHECK(M32(d[p]+0x1840)==1);
            CHECK(M32(d[p]+0x1844)==0 && M32(d[p]+0x1848)==0);
        } else CHECK(M32(d[p]+0x18)==0xa5a5a5a5 && M32(d[p]+0x1c)==0xa5a5a5a5 && M32(d[p]+0x20)==0xa5a5a5a5);
    }
    CHECK(backend_inits==1); CHECK(M32(0x9203c0)==2);
    M32(0x982bec)=1;
    // Ten live presets, keyboard-only / mouse-shaped empty slots, mixed bindings,
    // all controller types and maximum-of-four behavior. Controller bindings even
    // in slots 0/1 route by their type, never by the binding-record position.
    for(int type=1;type<=10;type++) {
        s_type.value=type-1;
        memset(G+0x91f418,0,76*52);
        bind(30,0,1,9,0); bind(31,0,10,0,0); // keyboard and unsupported/mouse cannot reach P2
        bind(32,0,1,9,0); bind(32,2,2,7,1); // keyboard wins P1; P2 ignores it
        for(int t=2;t<=9;t++) bind(40+t,2,t,0,t==3?1:0);
        bind(60,0,2,0,0); bind(60,1,2,0,1);
        bind(0,2,5,0,2); bind(1,2,7,0,2);
        float previous[76]={0};
        for(int frame=0;frame<8;frame++) {
            fake_frame(frame);
            uint8_t saved[76*52]; memcpy(saved,G+0x91f418,sizeof saved);
            int before=polls;
            Cpu c=cpu(d[0]); f_006349B0(&c); abi(&c,0); CHECK(polls==before+1);
            // Compare port 0 itself across the one-pad baseline and two-pad mode.
            fwrite(G+d[0],1,0x1858,p0); fwrite(&c,1,sizeof c,p0);
            if(pad_count()==2) {
                uint8_t first[0x1858]; memcpy(first,G+d[0],sizeof first);
                int k=key_reads,m=mouse_reads,f=follows,cap=capture_calls,read0=physical_reads[0];
                c=cpu(d[1]); f_006349B0(&c); abi(&c,0); CHECK(c.top==0);
                CHECK(polls==before+1); CHECK(key_reads==k && mouse_reads==m);
                CHECK(follows==f && capture_calls==cap && physical_reads[0]==read0);
                CHECK(!memcmp(first,G+d[0],sizeof first));
                CHECK(value(1,30)==0 && value(1,31)==0);
                CHECK(value(0,30)==1 && value(0,32)==1);
                CHECK(value(1,32)==.25f);
                CHECK(value(0,44)==0 && value(1,44)==(frame%2?0:1));
                CHECK(value(0,45)>.49f && value(0,45)<.51f);
                CHECK(value(1,45)==(frame%2?1:0));
                CHECK(value(1,60)==(frame%2? .25f:1));
                for(int t=2;t<=9;t++) CHECK(fabsf(value(1,40+t)-eval(40+t,2,1))<1e-6f);
                CHECK(!memcmp(saved,G+0x91f418,sizeof saved));
                if(frame) for(int a=0;a<76;a++) CHECK(MF32(M32(d[1]+0x1c)+4*a)==previous[a]);
                for(int a=0;a<76;a++) previous[a]=value(1,a);
            } else {
                c=cpu(d[1]); f_006349B0(&c); abi(&c,0); CHECK(polls==before+1);
                CHECK(M32(d[1]+0x20)==0xa5a5a5a5);
            }
            CHECK(!memcmp(saved,G+0x91f418,sizeof saved)); CHECK(swaps==0);
            sample(out,&c);
        }
    }
    if(pad_count()==2) {
        // Disabled/capture P2 must neither read input nor mutate value buffers.
        uint8_t values[0x800]; memcpy(values,G+d[1]+0x1034,sizeof values);
        for(int capture=0;capture<2;capture++) {
            M32(0x982bec)=capture; M8(d[1]+0x183c)=capture;
            int k=key_reads,m=mouse_reads,p=polls,r=physical_reads[1];
            Cpu c=cpu(d[1]); f_006349B0(&c); abi(&c,0);
            CHECK(k==key_reads && m==mouse_reads && p==polls && r==physical_reads[1]);
            CHECK(!memcmp(values,G+d[1]+0x1034,sizeof values));
        }
        M8(d[1]+0x183c)=0; M32(0x982bec)=1;
        // Disconnect/reconnect: missing physical slot is neutral, previous preserved.
        M32(0x91f154)=0;
        Cpu c=cpu(d[1]); f_006349B0(&c); abi(&c,0);
        for(int a=0;a<76;a++) CHECK(value(1,a)==0);
        M32(0x91f154)=0x101100; c=cpu(d[1]); f_006349B0(&c); abi(&c,0);
        CHECK(value(1,32)==.25f);
    }
done:
    printf("padguest NFS_PADS=%s: %d checks, %d failures; polls=%d init=%d\n",
        getenv("NFS_PADS")?:"unset",checks,failures,polls,backend_inits);
    fclose(out); fclose(p0); free(G); return failures!=0;
}
'''


def run(cmd, **kwargs):
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        print((result.stdout + result.stderr)[-6000:])
        raise RuntimeError(f'{cmd[0]} exited {result.returncode}')
    return result.stdout


def main():
    bodies = '\n'.join(translated(file, name) for file, names in (
        ('speed_31.c', ('00634B50', '006349B0')),
        ('speed_30.c', ('00628940', '006284A0')),
    ) for name in names)
    pad = re.search(r'int pad_count\(void\) \{.*?\n\}', (ROOT/'runtime/user32.m').read_text(), re.S).group()
    hook = (ROOT/'runtime/controltype.c').read_text()
    # git/xcrun may warn on stderr under the offline runner's scratch TMPDIR.
    # Keep that diagnostic stream out of the C source read from git stdout.
    baseline = run(['git', 'show', BASELINE + ':runtime/controltype.c'], cwd=ROOT)
    baseline += '\nvoid f_00634B50(Cpu *c) { f_00634B50_orig(c); }\n'
    mutants = {
        'alias-slot-zero': hook.replace('M32(r + 4) = M32(dev + 0x24);', 'M32(r + 4) = 0;'),
        'skip-second-buffer-clear': hook.replace('memset(G + dev + 0x1034, 0, 0x800);',
                                               'memset(G + dev + 0x1034, 0, 0x400);'),
    }
    assert all(source != hook for source in mutants.values())
    with tempfile.TemporaryDirectory(prefix='padguest-') as tmp:
        tmp = Path(tmp)
        def compile_one(name, source):
            c = tmp/(name+'.c'); exe = tmp/name
            # include paths resolve both generated tables and real runtime headers.
            c.write_text(PRE + pad + bodies + source + MAIN)
            run(['clang', '-std=c11', '-g', '-w', '-DRECOMP_ABICHECK', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-I', str(ROOT/'recomp'), '-I', str(ROOT/'recomp/out'),
                 '-I', str(ROOT/'runtime'), str(c), '-o', str(exe)])
            return exe
        old = compile_one('before', baseline)
        new = compile_one('after', hook)
        single_p0 = None
        for mode in (None, '1', 'invalid', '2'):
            env = dict(os.environ); env.pop('NFS_PADS', None)
            env.pop('ASAN_OPTIONS', None); env['UBSAN_OPTIONS']='halt_on_error=1'
            if mode is not None: env['NFS_PADS'] = mode
            print(run([str(new), str(tmp/'new.bin')], env=env).strip())
            if mode == '1': single_p0 = (tmp/'new.bin.p0').read_bytes()
            if mode == '2':
                assert single_p0 == (tmp/'new.bin.p0').read_bytes()
                print(f'  port 0: 80 snapshots byte-identical across one/two-pad modes ({len(single_p0)} bytes)')
            if mode != '2':
                run([str(old), str(tmp/'old.bin')], env=env)
                assert (tmp/'new.bin').read_bytes() == (tmp/'old.bin').read_bytes(), mode
                print(f'  baseline {mode or "unset"}: 80 snapshots byte-identical ({(tmp/"new.bin").stat().st_size} bytes)')
        for name, source in mutants.items():
            exe = compile_one(name, source)
            result = subprocess.run([str(exe), str(tmp/'mutant.bin')], env=dict(os.environ, NFS_PADS='2'),
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            assert result.returncode == 1 and 'failures;' in result.stdout, result.stdout[-2000:]
            print(f'  rejected {name}: {result.stdout.splitlines()[-1]}')
    print('padguest_test: PASS')


if __name__ == '__main__':
    main()
