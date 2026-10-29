"""Offline MainLAN visibility/stack proof; no game, sockets or GUI.
Measured: five cases x 9 checks = 45/0; hidden-LAN control 45/1;
dead-Online-visible control 45/2. Both controls fail. Actual AddOption hook.
"""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parent.parent
HARNESS=r'''
#include "host.h"
#include "settings.h"
#include <stdlib.h>
#include <string.h>
// These legacy menu cases have no saved setting; lan_toggle_test covers the registry.
Setting *settings_find(const char *id) { (void)id; return NULL; }
uint8_t *G; int diag_on=1;
static unsigned called,checks,failed;
void diag_event(const char *s,...) {(void)s;}
void f_00573960_orig(Cpu *c) {++called;c->esp+=8;}
SOURCE
int main(int argc,char **argv) {
    if(argc!=3)return 2;
    unsigned want_lan=atoi(argv[1]),want_online=atoi(argv[2]);
    G=calloc(1,0x10000);
    uint32_t names[]={0xD6DB74DE,0x74FB90A8,0x5815A2B5};
    for(unsigned i=0;i<3;i++){
        M32(0x2000+0x2c)=names[i];M32(0x1004)=0x2000;
        Cpu c={.esp=0x1000,.ecx=0x3000};unsigned before=called;
        f_00573960(&c);
        ++checks;failed+=called-before!=(i==0?want_lan:i==1?want_online:1);
        ++checks;failed+=c.esp!=0x1008;
        ++checks;failed+=c.ecx!=0x3000;
    }
    printf("LAN MENU %s: %u checks, %u failures\n",failed?"FAIL":"PASS",checks,failed);
    free(G);return failed!=0;
}
'''

def main():
    s=(ROOT/'runtime/fe_console.c').read_text();s=s[s.index('// MainLAN follows'):s.index('// Options > Audio')]
    cases=[('off',None,None,'0','0'),('lan','1',None,'1','0'),('invalid','01',None,'0','0'),
           ('legacy',None,'1','1','1'),('both','1','1','1','0')]
    with tempfile.TemporaryDirectory(prefix='lan-menu-') as td:
        tmp=Path(td)
        for variant,text in [('normal',s),('hidden',s.replace('!lan_items() && !online_items()', '!online_items()')),
                             ('dead-online',s.replace('lan_items() || !online_items()', '!lan_items() && !online_items()'))]:
            unit=tmp/'test.c';unit.write_text(HARNESS.replace('SOURCE',text));exe=tmp/variant
            p=subprocess.run(['clang','-O1','-Iruntime','-Irecomp',str(unit),'-o',str(exe)],cwd=ROOT,capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2000:]
            bad=0
            for label,lan,legacy,a,b in cases:
                env=dict(os.environ);env.pop('NFS_LAN',None);env.pop('NFS_FE_ONLINE_ITEMS',None)
                if lan:env['NFS_LAN']=lan
                if legacy:env['NFS_FE_ONLINE_ITEMS']=legacy
                p=subprocess.run([str(exe),a,b],env=env,capture_output=True,text=True)
                bad+=p.returncode!=0
                print(variant,label,p.stdout.strip(),flush=True)
            assert (bad==0)==(variant=='normal'),(variant,bad)
    return 0
if __name__=='__main__':raise SystemExit(main())
