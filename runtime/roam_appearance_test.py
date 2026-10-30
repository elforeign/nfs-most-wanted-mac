"""Production car/paint gather, validation and spawn. Proof: normal 19/0 failures; removed parts 19/1; removed paint 19/1; removed gather 19/7; clone-zero label 19/2."""
from pathlib import Path
import subprocess
import tempfile
RT=Path(__file__).resolve().parent
CF=['clang','-std=c11','-D_DARWIN_C_SOURCE','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-I',str(RT),'-I',str(RT.parent/'recomp')]
def main():
    mutants={'normal':None,
             'parts':('M16(custom + 2*i) = part ? (uint16_t)part_index : 0xffffu;', 'M16(custom + 2*i) = part ? 0xffffu : 0xffffu;'),
             'paint':('memcpy(G + custom + 0x138, remote->look.parts + 70, 22 * 4);',''),
             'gather':('look.nparts = ROAM_LOOK_CUSTOM_WORDS;','look.nparts = 0;'),
             'labels':('p->drawable = clone < 256;', 'p->drawable = roam_car_find(r->id, 0) != NULL;')}
    with tempfile.TemporaryDirectory(prefix='roam-appearance-') as tmp:
        td=Path(tmp)
        for label,mutant in mutants.items():
            src=(RT/'roam_game.c').read_text()
            if mutant:
                assert src.count(mutant[0])==1
                src=src.replace(*mutant)
            (td/'roam_game.c').write_text(src)
            for file in ['roam_switch_test.c','roam_appearance_test.c']:(td/file).write_text((RT/file).read_text())
            exe=td/label
            p=subprocess.run(CF+[str(td/'roam_appearance_test.c'),str(RT/'roam_core.c'),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2500:]
            p=subprocess.run([str(exe)],capture_output=True,text=True,timeout=15)
            print(label+': '+p.stdout.strip(),flush=True)
            assert p.returncode==(0 if label=='normal' else 1),p.stderr[-2500:]
    return 0
if __name__=='__main__':raise SystemExit(main())
