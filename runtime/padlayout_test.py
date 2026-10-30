#!/usr/bin/env python3
"""Mixed-layout P2 bindings: real defaults + control hook + translated evaluator.
ASan/UBSan, retail PE table and translated Xbox defaults; fake physical state only.
PASS: 1,688 checks / 0 failures. Old shared-layout mutant: 80 / 1,688
failures; missing restore: 8 / 1,688 failures; P1 identity reused for P2 pedals:
16 / 1,688 failures (all rejected). Mixed PlayStation/Xbox pairs; no game/hardware.
"""
from pathlib import Path
import importlib.util
import os
import re
import struct
import subprocess
import tempfile

R = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('padguest_harness', R/'padguest_test.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

EXTRA = r'''
static int xbox_layout, second_ps;
int user32_pad_layout_xbox(void) { return xbox_layout; }
int pad_identity_n(int idx,char *name,size_t n) {
    (void)idx; snprintf(name,n,"%s",second_ps ? "Controller (DualSense)" : "Controller (Xbox)");
    return second_ps ? PAD_KIND_DUALSENSE : PAD_KIND_OTHER;
}
static void dump(const char *why) { (void)why; }
void f_00460BC0(Cpu *c) {
    uint32_t p=arg(c,0), hash=0xffffffff;
    while(M8(p)) { unsigned ch=M8(p++); if(ch>='a' && ch<='z') ch-=32; hash=hash*33+ch; }
    c->eax=hash; c->esp+=4;
}
void f_00628530(Cpu *c) { c->eax=0; c->esp+=4; }
'''
MAIN = r'''
int main(int argc,char **argv) {
    (void)argc;
    G=calloc(1,0x1000000); FILE *f=fopen(argv[1],"rb"); if(!f) return 2;
    if(fread(G,1,0x1000000,f)!=0x1000000) return 2; fclose(f);
    MF32(0x890968)=0; MF32(0x89096c)=1; MF32(0x8a7a58)=32767.5f;
    MF32(0x8a7a5c)=.0078125f; MF32(0x8957bc)=3.0518044e-05f;
    for(int p=0;p<2;p++) { Cpu c=cpu(d[p]); M32(c.esp+4)=p; f_00634B50(&c); }
    M32(0x982bec)=1;
    int accept=-1,cancel=-1;
    for(int a=0;a<76;a++) {
        const char *name=(const char *)G+M32(GA_EXE+52*a);
        if(!strcmp(name,"FE_ACTION_ACCEPT")) accept=a;
        if(!strcmp(name,"FE_ACTION_CANCEL")) cancel=a;
    }
    if(accept<0 || cancel<0) { printf("missing FE actions %d %d\n",accept,cancel); return 2; }
    // PS P1 / non-PS P2 and its reverse, both non-PS layout choices, all ten presets.
    for(int ps0=0;ps0<2;ps0++) for(xbox_layout=0;xbox_layout<2;xbox_layout++) {
        M32(JOY)=0x101000;
        strcpy(joy_name(joy()),ps0 ? "Controller (DualSense)" : XBOX_NAME);
        second_ps=!ps0;
        Cpu c=cpu(d[0]); f_0063CD80(&c);
        for(int type=1;type<=10;type++) {
            s_type.value=type-1;
            for(int pressed=0;pressed<3;pressed++) {
                memset(fake_pad,0,sizeof fake_pad);
                // Native PS Cross=0, Triangle=3. Non-PS layout emits Accept=1, Cancel=2.
                int obj=pressed==1 ? (second_ps?0:1) : (second_ps?3:2);
                if(pressed) fake_put(1,0xc+4*obj,128);
                for(int p=0;p<2;p++) memcpy(G+0x121000+0x1000*p,fake_pad[p],0x224);
                uint8_t saved[76*52]; memcpy(saved,G+GA,sizeof saved);
                c=cpu(d[1]); f_006349B0(&c); abi(&c,0);
                CHECK(value(1,accept)==(pressed==1)); CHECK(value(1,cancel)==(pressed==2));
                CHECK(!memcmp(saved,G+GA,sizeof saved)); CHECK(swaps==0);
            }
        }
        // Same-layout custom binding still wins without a defaults swap.
        second_ps=ps0; set_rec(rec(accept,2),T_BUTTON,0,15);
        CHECK(padbind_port_begin(1)==0); CHECK(M32(rec(accept,2)+8)==15);
        // Trigger preset follows each port's identity, including mixed layouts.
        second_ps=!ps0; s_type.value=0;
        for(int pressed=0;pressed<2;pressed++) {
            memset(fake_pad,0,sizeof fake_pad);
            fake_put(1,8,32768); fake_put(1,0x214,49151);
            if(pressed) fake_put(1,0xc,128); // native Cross
            for(int p=0;p<2;p++) memcpy(G+0x121000+0x1000*p,fake_pad[p],0x224);
            uint8_t saved[76*52]; memcpy(saved,G+GA,sizeof saved);
            s_pedals.value=0; c=cpu(d[1]); f_006349B0(&c);
            float prior[76]; for(int a=0;a<76;a++) prior[a]=value(1,a);
            s_pedals.value=1; c=cpu(d[1]); f_006349B0(&c); abi(&c,0);
            CHECK(padbind_pedals(1)==second_ps);
            if(second_ps) {
                CHECK(fabsf(value(1,0)-.5f)<.0001f && fabsf(value(1,1))<.0001f);
                CHECK(value(1,8)==pressed && !value(1,7));
            } else for(int a=0;a<76;a++) CHECK(value(1,a)==prior[a]);
            CHECK(!memcmp(saved,G+GA,sizeof saved)); CHECK(swaps==0);
        }
        s_pedals.value=0;
    }
    printf("padlayout: %d checks, %d failures\n",checks,failures);
    free(G); return failures!=0;
}
'''


def main():
    exe = Path(os.environ.get('NFS_GAME', str(R.parent.parent/'Need For Speed Most Wanted Black Edition')))/'speed.exe'
    data=exe.read_bytes(); pe=struct.unpack_from('<I',data,0x3c)[0]
    ns=struct.unpack_from('<H',data,pe+6)[0]; opt=struct.unpack_from('<H',data,pe+20)[0]
    base=struct.unpack_from('<I',data,pe+52)[0]; memory=bytearray(0x1000000)
    for i in range(ns):
        _,vs,va,rs,ro=struct.unpack_from('<8sIIII',data,pe+24+opt+40*i)
        memory[base+va:base+va+rs]=data[ro:ro+rs]
    pb=(R/'padbind.c').read_text()
    defs=pb[pb.index('#define GA '):pb.index('static uint32_t name_hash')]
    helpers=pb[pb.index('static int name_ps('):pb.index('static const char *action_name(')]
    defaults=pb[pb.index('// ---------------- defaults'):pb.index('// ---------------- the sets')]
    pre=re.sub(r'int padbind_port_begin.*?\nvoid padbind_follow', 'void padbind_follow',h.PRE,flags=re.S)
    pre=re.sub(r'\bput\(', 'fake_put(', pre)
    pre=pre.replace('int padbind_pedals(int port) { (void)port; return 0; }', '')
    pre+='\n#define PEDALS_NO_TRACE 1\n#include "pedals.c"\n'
    pad=re.search(r'int pad_count\(void\) \{.*?\n\}',(R/'user32.m').read_text(),re.S).group()
    bodies='\n'.join(h.translated(file,name) for file,names in (
        ('speed_31.c',('00634B50','006349B0','0063CD80')),
        ('speed_30.c',('00628940','006284A0')),
    ) for name in names)
    hook=(R/'controltype.c').read_text()
    sources={'normal':hook,'old-shared-layout':hook.replace('padbind_port_begin(1)', '0'),
             'primary-pedals':hook.replace('int pedal_layout = padbind_pedals(port);', 'int pedal_layout = padbind_pedals(0);'),
             'missing-restore':hook.replace('padbind_port_end(port_swapped);','(void)port_swapped;')}
    with tempfile.TemporaryDirectory(prefix='padlayout-') as tmp:
        tmp=Path(tmp); (tmp/'memory').write_bytes(memory)
        for name,source in sources.items():
            path=tmp/(name+'.c'); out=tmp/name
            path.write_text('\n'.join((pre,pad,defs,helpers,EXTRA,defaults,bodies,source,MAIN)))
            h.run(['clang','-std=c11','-g','-w','-DRECOMP_ABICHECK','-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all','-I',str(R),'-I',str(R.parent/'recomp'),
                   '-I',str(R.parent/'recomp/out'),str(path),'-o',str(out)])
            p=subprocess.run([out,tmp/'memory'],env=dict(os.environ,NFS_PADS='2'),capture_output=True,text=True)
            if name=='normal':
                assert p.returncode==0,p.stdout[-2000:]+p.stderr[-2000:]
            else:
                assert p.returncode==1 and 'failures' in p.stdout,p.stdout[-2000:]+p.stderr[-2000:]
            print(name+': '+p.stdout.strip().splitlines()[-1])
    print('padlayout_test: PASS')

if __name__=='__main__': main()
