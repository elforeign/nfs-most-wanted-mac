"""Shared endpoint controller, fake router/server backends; no sockets.
Before: 125/0, server starts/stops 20/20, mapping deletes 16, address code TE29-43A2W.
After: 59/0 normal; router/listener/endpoint/room/unconfigured mutants: 1/1/3/2/31 failures.
Banned status uses reserved synthetic error 5; the current relay drops bans silently.
Normal router/embedded calls 0/0; restored setup/listener each makes 3 forbidden calls.
"""
from pathlib import Path
import re
import subprocess
import tempfile
RT=Path(__file__).resolve().parent
CF=['clang','-std=c11','-D_DARWIN_C_SOURCE','-Wall','-Wextra','-Werror','-g','-pthread',
    '-fsanitize=address,undefined','-I',str(RT)]
def main():
    source=(RT/'roam_online.c').read_text()
    harness=(RT/'roam_session_test.c').read_text()
    mutants={
        'normal':None,
        'router':('reset(); mode = create ? 1 : 2;', 'NatMap m = {0}; natmap_open(&m, 47800, NULL); reset(); mode = create ? 1 : 2;'),
        'listener':('reset(); mode = create ? 1 : 2;', '(void)roam_server_start(0x7f000001, 0); reset(); mode = create ? 1 : 2;'),
        'endpoint':('snprintf(target_host, sizeof target_host, "%s", host);','snprintf(target_host, sizeof target_host, "%s", "198.51.100.1");'),
        'room':('target_room = room;', 'target_room = 1;'),
        'unconfigured':('if (!endpoint(host, &port))', 'if (!endpoint(host, &port) && 0)'),
    }
    with tempfile.TemporaryDirectory(prefix='roam-session-') as tmp:
        td=Path(tmp)
        for name,mutation in mutants.items():
            text=source
            if mutation:
                old,new=mutation; assert text.count(old)==1
                text=text.replace(old,new)
            src=td/(name+'.c'); src.write_text(text)
            test=td/(name+'_test.c'); test.write_text(harness.replace('"roam_online.c"','"'+str(src)+'"'))
            exe=td/name
            p=subprocess.run(CF+[str(test),str(RT/'roam_code.c'),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-3000:]
            p=subprocess.run([str(exe)],capture_output=True,text=True,timeout=15)
            print(name+': '+p.stdout.strip(),flush=True)
            assert not re.search(r'(?:\d{1,3}\.){3}\d{1,3}|R-[A-Z2-9]{4}-[A-Z2-9]{5}',p.stderr), 'session log leak'
            assert p.returncode==(1 if mutation else 0),p.stderr[-3000:]
    return 0
if __name__=='__main__': raise SystemExit(main())
