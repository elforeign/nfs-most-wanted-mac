"""Production relay names/LOOK; fake send transport. Proof: normal 53/0 failures; name removal 53/1; LOOK corruption 53/1; sanitizer removal 53/2."""
from pathlib import Path
import subprocess
import tempfile
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g',
      '-fsanitize=address,undefined', '-I', str(RT)]
def main():
    mutants = {'normal': None,
               'names': ('roam_relay.c', 'memcpy(name + len, o->name, 16);', 'memset(name + len, 0, 16);'),
               'look': ('roam_relay.c', 'len += roam_look_put(look + len, &o->look);', 'o->look.car = 0; len += roam_look_put(look + len, &o->look);'),
               'sanitize': ('roam_core.c', "? in[i] : '?'", '? in[i] : in[i]')}
    with tempfile.TemporaryDirectory(prefix='roam-identity-') as tmp:
        td = Path(tmp)
        for label, mutant in mutants.items():
            for file in ['roam_identity_test.c','roam_relay.c','roam_core.c']:
                src = (RT/file).read_text()
                if mutant and mutant[0]==file:
                    assert src.count(mutant[1]) == 1
                    src = src.replace(mutant[1],mutant[2])
                (td/file).write_text(src)
            exe = td/label
            p = subprocess.run(CF+[str(td/'roam_identity_test.c'),str(td/'roam_core.c'),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode == 0,p.stderr[-2500:]
            p = subprocess.run([str(exe)],capture_output=True,text=True,timeout=15)
            print(label+': '+p.stdout.strip(),flush=True)
            assert p.returncode == (0 if label=='normal' else 1),p.stderr[-2500:]
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
