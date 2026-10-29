"""Offline discovery diagnostic proof, autodiscovered; no game/socket launch.
Runs the actual translated 0083ECF0 snapshot wrapper, 0084CDE0 cache decoder and
0084C580 kind comparator in a synthetic cache. Scheduler/allocator/imports are
fakes; query results are NOT canned. Measured for both NFSMWNA and NFSMW:
normal 13/0; disabled 13/4; bad frame and empty-kind 13/3; observer-off
and wrong room-offset 13/1. All controls exit 1 (both region kinds).
Live discovery PENDING: runtime/lan_check.sh /tmp/lana2-kind
"""
from pathlib import Path
import re
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parent.parent
HARNESS = r'''
#include "host.h"
#include "funcs.h"
#include <time.h>
#include <unistd.h>
uint64_t fake_clock(clockid_t);
#define clock_gettime_nsec_np fake_clock
#include "SOURCE"
#undef clock_gettime_nsec_np
uint8_t *G;
int diag_on;
uint32_t last_error;
uint32_t ws2_local_ip = 0x7F000001;
int ws2_trace, ws2_trace_listening, ws2_trace_accepts;
uint16_t ws2_trace_port, ws2_trace_peers[64];
const char *server_import_names[256];
uint32_t server_import_calls[256];
static uint64_t tick;
static unsigned init, allocated, snapshots, nonempty, selectors, bad;
static const char *region_kind;
uint64_t fake_clock(clockid_t clock) { (void)clock; return tick * 1000000; }
uint32_t galloc(uint32_t n) { (void)n; return 0x10000; }
uint32_t server_handle(void) { return 0; }
int threads_module_running(uint32_t lo, uint32_t hi, uint32_t *start) { (void)lo; (void)hi; *start=0; return 0; }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    c->eax=0xdead; c->ecx=0xbeef; c->esp-=20;
    if (fn==0x0083CD40) { bad += n!=3 || a[0]!=0x6f70656e; return 0; }
    if (fn==0x0083CCC0) { bad += n!=1 || G[a[0]]!=0; ++init; return 0; }
    if (fn==0x0064A3D0) {bad+=n!=0; ++selectors; return 0x21000;}
    if (fn==0x0083ED50) {
        bad += n!=0; ++allocated;
        M32(0x200A4)=0x22000;
        M32(0x22028)=0x23000;M32(0x2202C)=0x231A4;
        strcpy((char *)G+0x23008,region_kind);
        strcpy((char *)G+0x23028,"LanHost");
        strcpy((char *)G+0x23048,"9900|0");
        strcpy((char *)G+0x23108,"TCP:~1:1024\tUDP:~1:1024");
        strcpy((char *)G+0x23184,"127.0.0.1");
        return 0x20000;
    }
    if (fn==0x0084CDE0) {
        bad+=n!=6;Cpu query={.esp=0x38000};
        for(int i=0;i<6;i++)M32(query.esp+4+4*i)=a[i];
        f_0084CDE0(&query);return query.eax;
    }
    if (fn==0x0083ECF0) {
        bad += n!=4 || a[0]!=0x20000 || a[1]!=0x21000 || a[2]!=0x10000 || a[3]!=2048;
        ++snapshots;
        if (n!=4 || a[0]!=0x20000) return 0;
        Cpu query={.esp=0x38000};
        for (int i=0;i<4;++i) M32(query.esp+4+4*i)=a[i];
        f_0083ECF0(&query);
        nonempty += query.eax==2 && strstr((char *)G+a[2],"LanHost") && strstr((char *)G+a[2],"127.0.0.1");
        return query.eax;
    }
    ++bad; return 0;
}
void f_007C6610(Cpu *c) {
    const char *base=(char *)G+arg(c,0), *found=strstr(base,(char *)G+arg(c,1));
    c->eax=found ? (uint32_t)(found-(char *)G) : 0;c->esp+=4;
}
void f_007D6CB0(Cpu *c) {c->esp+=4;} // security cookie helper; cache path unchanged
void f_0084D1B0(Cpu *c) {++bad;c->eax=0;c->esp+=4;} // cache already constructed by fixture
int main(int argc,char **argv) {
    region_kind=argc>1 ? argv[1] : "NFSMWNA";
    G=calloc(1,0x1000000);
    strcpy((char *)G+0x21000,region_kind); setenv("BOOT_DIAG_LANDISCOVERY","1",1);
    Cpu c={.esp=0x30000,.eax=123,.ecx=456}, saved=c;
    unsigned failures=0, checks=0;
#define CHECK(x) do { ++checks; failures += !(x); } while(0)
    discovery_pass(&c); CHECK(!init && !snapshots);
    tick=7999; discovery_pass(&c); CHECK(!init && !snapshots);
    tick=8000; discovery_pass(&c); CHECK(init==1 && allocated==1 && snapshots==1 && nonempty==1 && selectors==1);
    tick=10999; discovery_pass(&c); CHECK(snapshots==1);
    tick=11000; discovery_pass(&c); CHECK(init==1 && allocated==1 && snapshots==2 && nonempty==2 && selectors==1);
    CHECK(!bad); CHECK(!memcmp(&c,&saved,sizeof c));
    setenv("NFS_LAN","1",1);
    M32(0x0091CADC)=0x1000;M32(0x1000)=0x1100;M32(0x1100+0xE4)=0x1200;
    M32(0x1200+0x20)=1;M32(0x1200+0xC)=0x1300;strcpy((char *)G+0x1300,"UI_PC_LAN_ServerSelect.fng");
    M32(0x009C3878)=0x22000;
    M32(0x009B41C4)=0x24000;M32(0x24000+0x38)=0x25000;M32(0x25000+8)=0x61757468;
    strcpy((char *)G+0x25000+0x15A8,"LanCopy");M32(0x25000+0x17E4)=3;strcpy((char *)G+0x25000+0x17EC,"LAN");
    FILE *log=tmpfile();fflush(stderr);int fd=dup(2);dup2(fileno(log),2);
    diag_on=0;original_lan_pass(&c);fflush(stderr);CHECK(ftell(log)==0);
    diag_on=1;original_lan_pass(&c);fflush(stderr);rewind(log);char lines[4096]={0};fread(lines,1,sizeof lines-1,log);
    CHECK(strstr(lines,"lan-ui: package UI_PC_LAN_ServerSelect.fng"));
    CHECK(strstr(lines,"auth=1 persona=LanCopy room-id=3 room=LAN"));
    CHECK(strstr(lines,"lan-ui: hosts rows 2 [LanHost"));
    CHECK(M32(0x25000+8)==0x61757468 && M32(0x25000+0x17E4)==3);
    CHECK(!memcmp(&c,&saved,sizeof c));
    dup2(fd,2);close(fd);fclose(log);
    printf("LAN DIAG %s: %u checks, %u failures\n", failures?"FAIL":"PASS",checks,failures);
    free(G); return failures!=0;
}
'''


def decoder():
    result=''
    for file,names in [('speed_56.c',['0083ECF0']),('speed_57.c',['0084C580','0084CDE0'])]:
        source=(ROOT/'recomp/out'/file).read_text()
        for name in names:
            match=re.search(r'void f_'+name+r'\(Cpu \*restrict c\) \{',source)
            assert match,name
            end=source.find('\nvoid ',match.end())
            result+=source[match.start():end]+'\n'
    return result


def main():
    source = (ROOT/'runtime/lanserver_selftest.c').read_text()
    with tempfile.TemporaryDirectory(prefix='lan-diag-') as td:
        tmp=Path(td)
        for name, text in [('normal', source), ('disabled', source.replace('if (!on || now_ms() < next) return;', 'if (1) return;')),
                           ('frame', source.replace('{object, kind, snapshot, 2048}', '{0, kind, snapshot, 2048}')),
                           ('empty-kind', source.replace('{object, kind, snapshot, 2048}', '{object, 0, snapshot, 2048}')),
                           ('observer-off', source.replace('if(!lan_diag_on() || now_ms()<next)return;', 'if(now_ms()<next)return;')),
                           ('room-offset', source.replace('(int32_t)M32(api+0x17E4)', '(int32_t)M32(api+0x17E0)'))]:
            unit=tmp/(name+'.c'); unit.write_text(text)
            harness=tmp/'harness.c'; harness.write_text(HARNESS.replace('SOURCE',str(unit))+decoder())
            exe=tmp/name
            p=subprocess.run(['clang','-O1','-Iruntime','-Irecomp','-Irecomp/out',str(harness),'-o',str(exe)],cwd=ROOT,capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2000:]
            for region in ['NFSMWNA','NFSMW']:
                p=subprocess.run([str(exe),region],capture_output=True,text=True)
                print(name,region,p.stdout.strip(),flush=True)
                assert p.returncode==(0 if name=='normal' else 1),(name,p.stdout,p.stderr)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
