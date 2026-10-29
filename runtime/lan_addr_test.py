"""Execute DirtySock's translated route helper with production loopback shim.
No real sockets/game. Catches 127.0.0.1's special destination-substitution path.
Measured 12/0; old route control 12/2 (host address and self comparison).
"""
from pathlib import Path
import re
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parent.parent
MAIN=r'''
void f_007D6CB0(Cpu *c){c->esp+=4;}
#define THUNK(name,fn) void f_##name(Cpu *c){win32_ws2(c,fn);}
THUNK(00862116,"#111") THUNK(0086212E,"#23") THUNK(0086215E,"#3")
THUNK(00862176,"#4") THUNK(008621AC,"#6") THUNK(008621B2,"#52")
THUNK(008621B8,"#57") THUNK(008621BE,"WSAIoctl")
FUNCTION
int main(void){
 G=calloc(1,0x1000000);setenv("NFS_LAN_LOOPBACK","1",1);unsetenv("NFS_LAN_COPY");
 M32(0x9C36F8)=0x202;
 const char *dest[]={"192.168.1.1","192.168.1.1","127.0.0.2","192.168.1.1"};
 uint32_t want[]={0x7F000001,0x7F000002,0x7F000001,0x7F000002};
 for(unsigned i=0;i<4;i++){
   if(i==1)setenv("NFS_LAN_COPY","1",1);else unsetenv("NFS_LAN_COPY");
   ws2_online=i==3;ws2_local_ip=i==3?0x7F000002:0x7F000001;
   address(dest[i],79);memset(G+0x4000,0,16);
   Cpu c={.esp=0x80000};uint32_t a[]={0x4000,16,0x2000,16};
   for(unsigned j=0;j<4;j++)M32(c.esp+4+4*j)=a[j];
   f_0084B770(&c);
   printf("route %u: %u.%u.%u.%u\n",i,G[0x4004],G[0x4005],G[0x4006],G[0x4007]);
   check(c.eax==0,"original helper returns success");check(M32(0x4004)==htonl(want[i]),"original helper returns own copy identity");
 }
 printf("LAN ADDR %s: %u checks, %u failures\n",failures?"FAIL":"PASS",checks,failures);
 return failures!=0;
}
'''
def main():
 generated=(ROOT/'recomp/out/speed_57.c').read_text();m=re.search(r'void f_0084B770\(Cpu \*restrict c\) \{',generated);assert m
 end=generated.find('\nvoid ',m.end());function=generated[m.start():end]
 policy=(ROOT/'runtime/ws2_policy_test.c').read_text().replace('int main(void)','int policy_main_unused(void)')
 source=(ROOT/'runtime/ws2_32.c').read_text()
 marker='if(identity==INADDR_LOOPBACK) { wsa_err=10051; RET(0xFFFFFFFF,9); }'
 variants=[('normal',source)]
 if marker in source:variants.append(('old-route',source.replace(marker,'if(0) { wsa_err=10051; RET(0xFFFFFFFF,9); }')))
 with tempfile.TemporaryDirectory(prefix='lan-addr-') as td:
  tmp=Path(td);unit=tmp/'test.c';unit.write_text(policy+MAIN.replace('FUNCTION',function))
  for name,text in variants:
   variant=tmp/(name+'.c');variant.write_text(text);exe=tmp/name
   p=subprocess.run(['clang','-O1','-Iruntime','-Irecomp','-Irecomp/out','-DWS2_C="'+str(variant)+'"',str(unit),'-o',str(exe)],cwd=ROOT,capture_output=True,text=True);assert p.returncode==0,p.stderr[-2000:]
   p=subprocess.run([str(exe)],capture_output=True,text=True);print(name,p.stdout.strip(),flush=True)
   assert p.returncode==(0 if name=='normal' else 1),(name,p.stdout,p.stderr)
 return 0
if __name__=='__main__':raise SystemExit(main())
