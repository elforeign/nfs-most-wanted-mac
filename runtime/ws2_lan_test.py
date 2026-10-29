"""Registered LAN proof: loopback by default; NFS_LAN_REAL=1 opts into real LAN.
One socket executable runs all argument-selected broken controls, avoiding a
firewall prompt per variant. Policy sockets remain fake in every mode.
"""
import os
from pathlib import Path
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parent.parent

# Test-only fault injection in a generated copy; production sources have no switches.
CHANGES = {
    'broadcast': ('r = setsockopt(fd, level, name, G + arg(c, 3), arg(c, 4));',
        'r = lan_fault("broadcast") && level == SOL_SOCKET && name == SO_BROADCAST ? 0 : setsockopt(fd, level, name, G + arg(c, 3), arg(c, 4));'),
    'reuse': ('discovery && setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one)',
        'discovery && !lan_fault("reuse") && setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one)'),
    'bind': ('a.sin_addr.s_addr = INADDR_ANY;\n                if (lan_copy()',
        'a.sin_addr.s_addr = lan_fault("bind") ? htonl(INADDR_LOOPBACK) : INADDR_ANY;\n                if (lan_copy()'),
    'identity': ('a->sin_addr.s_addr = htonl(0x7F000002);',
        'a->sin_addr.s_addr = htonl(lan_fault("identity") ? INADDR_LOOPBACK : 0x7F000002);'),
    'shift': ('s->bind_shift = discovery ? 0 : (port ? shift : 0);',
        's->bind_shift = discovery && !lan_fault("shift") ? 0 : (port ? shift : 0);'),
    'route': ('if (lan_copy()) me.sin_addr.s_addr = htonl(0x7F000002);',
        'if (lan_copy() && !lan_fault("route")) me.sin_addr.s_addr = htonl(0x7F000002);'),
    'target': ('a->sin_port = htons(9901);',
        'a->sin_port = htons(lan_fault("target") ? 9900 : 9901);'),
}


def main():
    source = (ROOT/'runtime/ws2_32.c').read_text()
    for name, (old, new) in CHANGES.items():
        assert source.count(old) == 1, (name, source.count(old))
        source = source.replace(old, new)
    pending = False
    with tempfile.TemporaryDirectory(prefix='ws2-lan-') as td:
        tmp=Path(td)
        variant=tmp/'ws.c'; variant.write_text(source)
        fault=tmp/'fault.h'; fault.write_text('#include <string.h>\nstatic const char *lan_fault_name;\n'
            'static int lan_fault(const char *s) { return lan_fault_name && !strcmp(s,lan_fault_name); }\n')
        executables={}
        for kind, harness in [('policy','ws2_policy_test.c'), ('socket','ws2_lan_test.c')]:
            text=(ROOT/'runtime'/harness).read_text().replace('int main(void)', 'int lan_test_main(void)')
            text += '\nint main(int argc,char **argv) { lan_fault_name=argc>1?argv[1]:"normal"; return lan_test_main(); }\n'
            unit=tmp/(kind+'.c'); unit.write_text(text)
            exe=tmp/('lan-'+kind)
            cmd=['clang','-O1','-Iruntime','-Irecomp','-Irecomp/out','-include',str(fault),
                 '-DWS2_C="'+str(variant)+'"',str(unit),'-o',str(exe)]
            p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2500:]
            executables[kind]=exe
        print('LAN MODE:', 'REAL (explicit NFS_LAN_REAL=1)' if os.environ.get('NFS_LAN_REAL')=='1' else 'LOOPBACK ONLY',
              '; one socket binary, variants selected by argument',flush=True)
        for name in ['normal',*CHANGES]:
            for kind, exe in executables.items():
                env=dict(os.environ)
                for key in ['NFS_LAN_COPY','NFS_ONLINE_OPEN','BOOT_ONLINE_PEER']:
                    env.pop(key,None)
                p=subprocess.run([str(exe),name],env=env,capture_output=True,text=True,timeout=25)
                expected=0 if name=='normal' else 1
                lines=[x for x in p.stdout.splitlines() if 'checks=' in x or ' checks, ' in x or
                       x.startswith(('WS2 POLICY ','SOCKET PENDING','LAN BIND AUDIT:'))]
                verdict='PENDING' if kind=='socket' and p.returncode==3 else 'PASS' if p.returncode==expected else 'FAIL'
                print(name,kind,verdict,'(expected exit',str(expected)+', got',str(p.returncode)+'):',
                      ' | '.join(lines),flush=True)
                if kind=='socket' and p.returncode==3 and 'SOCKET PENDING: run on a socket-enabled host' in p.stdout:
                    pending=True
                    continue
                assert p.returncode==expected,(name,kind,p.stdout[-2500:],p.stderr[-2500:])
                if kind=='socket' and os.environ.get('NFS_LAN_REAL')!='1':
                    binds=[x for x in p.stderr.splitlines() if x.startswith('LAN NATIVE BIND ')]
                    assert binds and all(x.startswith('LAN NATIVE BIND 127.0.0.1:') for x in binds),binds
        print('WS2 LAN DRIVER PASS: policy and seven rejected controls; native loopback binds audited' if not pending else
              'SOCKET PENDING: run on a socket-enabled host; non-socket controls PASS',flush=True)
        return 3 if pending else 0

if __name__=='__main__':
    raise SystemExit(main())
