"""Production HUD hook with fake FE; no game execution.
Proof: normal 209/0 failures; behind 209/2, icons 201/1, Off 222/1,
hidden 209/1, inherited parent 209/9, zero depth 209/1 (shown 0 vs 9), cache poison 209/6,
friend colour removed 209/1, stock colour restoration removed 209/1.
"""
from pathlib import Path
import subprocess
import tempfile
RT=Path(__file__).resolve().parent
CF=['clang','-std=c11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-I',str(RT),'-I',str(RT.parent/'recomp')]
def main():
    mutants={'normal':None,
             'behind':('clip[3] <= 0.01f','clip[3] == 0'),
             'icons':('hud_call(c,map,0x005675D0u,4,args);','(void)args;'),
             'off':('if (!online && !was_online) return;','(void)was_online; if (0) return;'),
             'hidden':('M32(label+0x1c) &= ~0x41u;',''),
             'restore_colour':('if (was_online) restore_icon_colours(map);', 'if (0) restore_icon_colours(map);'),
             'icon_colour':('M32(data)=255; M32(data+4)=239; M32(data+8)=112; M32(data+12)=255;', ''),
             'cache':('M32(label+0x58) = 0;', ''),
             'depth':('MF32(data+0x24)=1.0f;', ''),
             'parent':('M16(label+0x20) = 0;', 'M16(label+0x20) = 3;')}
    with tempfile.TemporaryDirectory(prefix='roam-hud-') as tmp:
        td=Path(tmp)
        for label,mutant in mutants.items():
            src=(RT/'roam_hud.c').read_text()
            if mutant:
                assert src.count(mutant[0])==1
                src=src.replace(*mutant)
            if label=='behind':
                src=src.replace('clip[2] < 0 || clip[2] > clip[3]', '0')
            (td/'roam_hud.c').write_text(src);(td/'roam_hud_test.c').write_text((RT/'roam_hud_test.c').read_text())
            exe=td/label
            p=subprocess.run(CF+[str(td/'roam_hud_test.c'),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2500:]
            p=subprocess.run([str(exe)],capture_output=True,text=True,timeout=15)
            print(label+': '+p.stdout.strip(),flush=True)
            assert p.returncode==(0 if label=='normal' else 1),p.stderr[-2500:]
    return 0
if __name__=='__main__':
    raise SystemExit(main())
