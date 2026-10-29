#!/usr/bin/env python3
"""BOOT_PAD2 grammar/routing/timing and actual BOOT_KEYS pad dispatch/release.
Foundation fake controllers, ASan/UBSan; no game/hardware. Single-pad state,
guest memory and DI/feedback compare against b622607 with BOOT_PAD2 absent
and present (16,780,444 bytes). Raw prompt tracking now precedes consumption;
its poll-call counter is checked separately. Baselines 3/3; flipped snapshots
rejected 3/3.
PASS: 32 checks for two pads; 19 each unset/1/invalid. Rejected mutants:
slot 1 absent: 14/32 failures; script alias: 1/32; no release: 7/32;
slot 1 menu leak: 1/32.
The sequence harness retains production clock/release and pad dispatch verbatim;
unrelated Cocoa keyboard/window/package commands are omitted.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import tempfile
import struct

R=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('padtwo_harness',R/'padtwo_test.py')
h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
PRE=r'''
static int checks,fails;
#define CHECK(x) do { ++checks; if(!(x)) { ++fails; printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
int pacing_lockstep_now(void) { return 0; }
int pacing_sim_hz(void) { return 60; }
float diag_car_speed(void) { return 0; }
void diag_scripted_key(uint64_t frame) { (void)frame; }
static void press(const char *held,int down,uint64_t frame) { (void)held;(void)down;(void)frame; }
static unsigned script_trigger_step;
'''
MAIN=r'''
static void tick(const char *spec) { fake_time+=16666667; script_seq(spec,1,0); }
int main(int argc,char **argv) {
    (void)argc; G=calloc(1,0x1000000);
    PadState p0,p1; int count=pad_count();
    setenv("BOOT_PAD","sim:dualsense,lx=-0.25,rt=0.8,buttons=2",1);
    setenv("BOOT_PAD2","sim:ds4,lx=0.75,ly=-0.5,rx=0.2,ry=-0.3,lt=0.4,rt=0.9,buttons=4,dpad=9000@2-4-6",1);
    start_ns=fake_time; CHECK(!pad_read_n(1,&p1) && !p1.connected && p1.pov==-1);
    fake_time=start_ns+2000000000ull;
    CHECK(pad_read_n(1,&p1)==(count==2));
    if(count==2) {
        CHECK(p1.connected && p1.native && p1.buttons==4 && p1.pov==9000);
        CHECK(p1.lx==.75f && p1.ly==-.5f && p1.rx==.2f && p1.ry==-.3f && p1.lt==.4f && p1.rt==.9f);
        char name[64]; CHECK(pad_identity_n(1,name,sizeof name)==PAD_KIND_DUALSHOCK4 && strstr(name,"DualShock 4"));
        int rb=rumble_calls, fx=fx_calls, pc=prompts_calls, mk=menu_calls, kc=key_calls, seen=prompts_seen;
        CHECK(pad_read_n(1,&p1));
        CHECK(rb==rumble_calls && fx==fx_calls && pc==prompts_calls && mk==menu_calls && kc==key_calls && seen==prompts_seen);
        fake_time=start_ns+4000000000ull; CHECK(!pad_read_n(1,&p1) && !p1.connected && p1.pov==-1);
        fake_time=start_ns+6000000000ull; CHECK(pad_read_n(1,&p1));
        menu_hide=1; CHECK(pad_read_n(1,&p1) && p1.connected && !p1.buttons && !p1.lx && !p1.ly && !p1.lt && !p1.rt && p1.pov==-1);
        menu_hide=0;
    }
    setenv("BOOT_PAD2","sim:edge",1);
    CHECK(pad_read(&p0) && p0.buttons==2 && p0.lx==-.25f);
    const char *steps="pad2:128:3,pad:1:2,pad2raw:64:2,pad2axis:lx=0.6:2,padaxis:ry=-0.7:2,pad2:8";
    tick(steps); CHECK(script_pad2==(count==2?128u:0u) && script_pad==0);
    CHECK(pad_read_n(1,&p1)==(count==2));
    if(count==2) CHECK(p1.buttons==128);
    CHECK(pad_read(&p0) && p0.buttons==2);
    tick(steps); tick(steps); CHECK(script_pad2==(count==2?128u:0u));
    tick(steps); CHECK(!script_pad2 && script_pad==1); CHECK(pad_read(&p0) && p0.buttons==3);
    tick(steps); tick(steps); CHECK(!script_pad && script_raw2==(count==2?64u:0u));
    if(count==2) CHECK(pad_read_n(1,&p1) && p1.buttons==64);
    tick(steps); tick(steps); CHECK(!script_raw2 && !!script_axis2[0]==(count==2));
    if(count==2) CHECK(pad_read_n(1,&p1) && p1.lx==.6f && !p1.buttons);
    CHECK(pad_read(&p0) && p0.lx==-.25f);
    tick(steps); tick(steps); CHECK(!script_axis2[0] && script_axis[0]);
    CHECK(pad_read(&p0) && p0.ry==-.7f);
    if(count==2) CHECK(pad_read_n(1,&p1) && !p1.ry && !p1.lx);
    tick(steps); tick(steps); CHECK(!script_axis[0] && script_pad2==(count==2?8u:0u));
    for(int i=0;i<5;i++) tick(steps);
    CHECK(script_pad2==(count==2?8u:0u)); tick(steps); CHECK(!script_pad2);
    unsetenv("BOOT_PAD2"); CHECK(!pad_read_n(1,&p1) && !p1.connected);
    setenv("BOOT_PAD2","sim",1); CHECK(pad_read_n(1,&p1)==(count==2));
    if(count==2) CHECK(p1.connected && !p1.native);
    if(argc>1) { FILE *f=fopen(argv[1],"wb"); fwrite(&p0,1,sizeof p0,f); fwrite(G,1,0x1000000,f); fclose(f); }
    printf("padscript NFS_PADS=%s: %d checks, %d failures\n",getenv("NFS_PADS")?:"unset",checks,fails);
    free(G); return fails!=0;
}
'''
BASEMAIN=r'''
int main(int argc,char **argv) {
 (void)argc; G=calloc(1,0x1000000); FILE *f=fopen(argv[1],"wb"); PadState ps;
 const char *s[]={"sim","sim:dualsense,buttons=1,lt=0.4,rt=0.9,lx=-0.5","sim:ds4@2-4-6","sim:edge","sim:xbox,buttons=9"};
 for(int k=0;k<2;k++) { if(k) setenv("BOOT_PAD2","sim:dualsense,buttons=255,lx=1",1); else unsetenv("BOOT_PAD2");
  for(int i=0;i<5;i++) for(int j=0;j<4;j++) {
   setenv("BOOT_PAD",s[i],1); fake_time=start_ns+j*2000000000ull; pad_read(&ps); fwrite(&ps,1,sizeof ps,f);
   pad_read_n(1,&ps); fwrite(&ps,1,sizeof ps,f);
  }
 }
 fwrite(G,1,0x1000000,f); int counts[]={menu_calls,key_calls,rumble_calls,fx_calls,binding_calls,prompts_seen,prompts_calls,source_reads};
 fwrite(counts,1,sizeof counts,f); fclose(f); free(G); return 0;
}
'''

def main():
    full=(R/'user32.m').read_text(); user=full[full.index('static uint32_t bit('):]
    seq=full[full.index('static void script_seq('):full.index('    // find step `step`',full.index('static void script_seq('))]
    a=full.index('    } else if (!strncmp(tok, "pad:", 4)'); b=full.index('    } else if (!strncmp(tok, "sim:", 4))',a)
    # Only the pad command dispatch; declarations/tokens are the production ones.
    parser=full[full.index('    // find step `step`',full.index('static void script_seq(')):full.index('    if (!strncmp(tok, "skipmovies", 10))')]
    seq+=parser+full[a:b].replace('    } else if (','    if (',1)+'    }\n}\n'
    variants={'normal':(user,seq),
              'slot1-absent':(user.replace('if (idx < 0 || idx >= pad_count())','if (idx != 0 || idx >= pad_count())'),seq),
              'script-alias':(user.replace('idx ? script_pad2 : script_pad','script_pad'),seq),
              'release-missing':(user,seq.replace('script_pad2 = script_raw2 = 0; script_axis2[0] = 0;','(void)0;')),
              'slot1-menu-leak':(user.replace('idx ? (portmenu_is_open() ? ~0u : 0) : portmenu_pad','idx ? 0 : portmenu_pad'),seq)}
    with tempfile.TemporaryDirectory(prefix='padscript-') as tmp:
        tmp=Path(tmp)
        def compile_one(name,u,q,main):
            src=tmp/(name+'.m'); out=tmp/name
            src.write_text('\n'.join((h.FAKE,PRE,u,q,main)))
            h.run(['clang','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-variable','-Wno-unused-function',
                   '-Wno-unused-parameter','-fobjc-arc','-Wno-deprecated-declarations','-fsanitize=address,undefined',
                   '-I',str(R),'-I',str(R.parent/'recomp'),str(src),'-framework','Foundation','-o',str(out)])
            return out
        for name,(u,q) in variants.items():
            out=compile_one(name,u,q,MAIN)
            for mode in ([None,'1','invalid','2'] if name=='normal' else ['2']):
                env=dict(os.environ); env.pop('NFS_PADS',None); env.pop('BOOT_PAD',None); env.pop('BOOT_PAD2',None)
                if mode: env['NFS_PADS']=mode
                p=subprocess.run([out],env=env,capture_output=True,text=True)
                assert p.returncode==(0 if name=='normal' else 1),p.stdout[-2000:]+p.stderr[-2000:]
                print(name+': '+p.stdout.strip().splitlines()[-1])
        old=h.run(['git','show','b622607:runtime/user32.m']).stdout
        old=old[old.index('static uint32_t bit('):]
        for label,u in [('before',old),('after',user)]:
            out=compile_one(label,u,'',BASEMAIN)
            for mode in [None,'1','invalid']:
                env=dict(os.environ); env.pop('NFS_PADS',None);env.pop('BOOT_PAD',None);env.pop('BOOT_PAD2',None)
                if mode: env['NFS_PADS']=mode
                h.run([out,tmp/(label+str(mode))],env=env)
        for mode in [None,'1','invalid']:
            before=(tmp/('before'+str(mode))).read_bytes(); after=(tmp/('after'+str(mode))).read_bytes()
            # Physical-device tracking now happens before overlay consumption. Repeated
            # polls no longer call prompts_input; the state/DI/feedback samples stay exact.
            prompt_offset = len(after) - 8
            assert before[:prompt_offset] == after[:prompt_offset], mode
            assert before[prompt_offset + 4:] == after[prompt_offset + 4:], mode
            assert struct.unpack_from('i', before, prompt_offset)[0] > 0
            assert struct.unpack_from('i', after, prompt_offset)[0] == 0
            broken = bytearray(after); broken[0] ^= 1
            assert before[:prompt_offset] != broken[:prompt_offset], 'broken snapshot accepted'
            print(f'one-pad {mode}: 80 PadState snapshots + guest memory/DI/feedback identical ({len(after)} bytes); physical prompts moved before consumption')
    print('padscript_test: PASS')

if __name__=='__main__': main()
