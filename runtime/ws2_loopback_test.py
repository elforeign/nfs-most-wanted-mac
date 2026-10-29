"""Production guarded LAN policy: fake host records all binds/listens/sends.
No game or sockets. Measured: 61/0; disabled clamp 61/6; sender 61/1;
fanout 61/1; implicit listen 61/1; broken game-peer target 61/2. Every control exits 1.
"""
import os
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parent.parent
PREFIX=r'''
static unsigned fixture_bad_bind,fixture_bad_send,fixture_listens;
static unsigned fixture_ports[64],fixture_send_ports[64];
static unsigned fixture_port_count,fixture_send_count;
static unsigned fixture_name_port=9999;
static uint32_t fixture_name_ip=0x7F000001;
static int fixture_listen(int fd,int backlog) {(void)fd;(void)backlog;++fixture_listens;return 0;}
'''
MAIN=r'''
int main(void) {
    G=calloc(1,0x1000000);
    unsetenv("NFS_LAN_LOOPBACK"); unsetenv("NFS_LAN_COPY");
    check(!lan_loopback(),"app default retains real LAN");
    setenv("NFS_LAN_LOOPBACK","1",1);setenv("NFS_ONLINE_OPEN","1",1);
    uint32_t s=invoke("#23",3,2u,2u,0u);
    const char *ips[]={"0.0.0.0","192.168.1.2","255.255.255.255","127.0.0.2"};
    for(unsigned i=0;i<4;++i) {
        address(ips[i],3658);
        check(invoke("#2",3,s,0x2000u,16u)==0 && last_bind.sin_addr.s_addr==htonl(INADDR_LOOPBACK),"all native binds loopback");
    }
    unsigned before=sockets, native_connects=connects;
    address("159.153.253.82",0);
    check(invoke("WSAIoctl",9,s,0xC8000014u,0x2000u,16u,0x4000u,16u,0u,0u,0u)==UINT32_MAX &&
        sockets==before && connects==native_connects,"route query falls back without native LAN socket");
    ws2_online=1;ws2_local_ip=0x7F000002;
    check(invoke("WSAIoctl",9,s,0xC8000014u,0x2000u,16u,0x4000u,16u,0u,0u,0u)==0 &&
        M32(0x4004)==htonl(0x7F000002),"A1 keeps configured identity");
    ws2_online=0;ws2_local_ip=INADDR_LOOPBACK;
    uint32_t h=invoke("#52",1,0u);
    check(h && M32(M32(M32(h+12)))==htonl(INADDR_LOOPBACK),"own hostname is loopback");
    invoke("#3",1,s);
    s=invoke("#23",3,2u,1u,0u);address("127.0.0.1",9900);
    check(invoke("#2",3,s,0x2000u,16u)==0 && last_bind.sin_addr.s_addr==htonl(INADDR_LOOPBACK),"lobby stays 127.0.0.1");
    check(invoke("#13",2,s,4u)==0 && fixture_listens==1,"bound loopback listener reaches native listen");
    fixture_name_ip=0;fixture_name_port=0;
    check(invoke("#13",2,s,4u)==UINT32_MAX && fixture_listens==1,"implicit ANY listener denied");
    fixture_name_ip=INADDR_LOOPBACK;fixture_name_port=9999;
    for(unsigned i=1;i<4;++i) {
        address(ips[i],9999);native_connects=connects;
        uint32_t r=invoke("#4",3,s,0x2000u,16u);
        check(i==3 ? r==0 : r==UINT32_MAX && connects==native_connects,"non-loopback connect denied even unsafe-open");
    }
    address("255.255.255.255",9999);before=sends;
    check(invoke("#20",6,s,0x3000u,384u,0u,0x2000u,16u)==UINT32_MAX && sends==before,"stream sendto cannot escape loopback");
    invoke("#3",1,s);
    setenv("NFS_LAN_COPY","1",1);
    s=invoke("#23",3,2u,2u,0u);address("0.0.0.0",9999);before=fixture_port_count;
    check(invoke("#2",3,s,0x2000u,16u)==0 && fixture_ports[before]==10001 &&
        ntohs(last_bind.sin_port)==10000,"copy 1 receiver 10001 and sender 10000");
    fixture_name_port=10001;M32(0x6100)=16;
    check(invoke("#6",3,s,0x6000u,0x6100u)==0 && ntohs(M16(0x6002))==9999,"guest receiver port remains 9999");
    struct sockaddr_in src={.sin_family=AF_INET,.sin_port=htons(10000),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    lan_source(sock_fd(s),&src);
    check(src.sin_addr.s_addr==htonl(0x7F000002) && ntohs(src.sin_port)==9999,"copy 1 self identity survives loopback");
    address("255.255.255.255",9999);before=fixture_send_count;
    check(invoke("#20",6,s,0x3000u,384u,0u,0x2000u,16u)==384 && fixture_send_count==before+2 &&
        fixture_send_ports[before]==9999 && fixture_send_ports[before+1]==10001,"broadcast fans out over loopback to both copies");
    address("192.168.1.2",9999);before=sends;
    check(invoke("#20",6,s,0x3000u,384u,0u,0x2000u,16u)==UINT32_MAX && sends==before,"LAN unicast sendto denied");
    struct sockaddr_in peer={.sin_family=AF_INET,.sin_port=htons(3658),.sin_addr.s_addr=htonl(0x7F000002)};
    lan_target(&peer);
    check(peer.sin_addr.s_addr==htonl(INADDR_LOOPBACK) && ntohs(peer.sin_port)==3659,"menu race target reaches copy 1 game port");
    lan_source(sock_fd(s),&peer);
    check(peer.sin_addr.s_addr==htonl(0x7F000002) && ntohs(peer.sin_port)==3658,"menu race source keeps advertised identity");
    ws2_online=1;peer.sin_addr.s_addr=htonl(INADDR_LOOPBACK);peer.sin_port=htons(3659);
    lan_source(sock_fd(s),&peer);
    check(peer.sin_addr.s_addr==htonl(INADDR_LOOPBACK) && ntohs(peer.sin_port)==3659,"A1 manual source peer stays unchanged");ws2_online=0;
    invoke("#3",1,s);
    check(!fixture_bad_bind && !fixture_bad_send,"native address audit: zero non-loopback binds/sendto");
    printf("GUARDED LAN %s: %u checks, %u failures; bad bind=%u send=%u\n",failures?"FAIL":"PASS",checks,failures,fixture_bad_bind,fixture_bad_send);
    free(G);return failures!=0;
}
'''


def main():
    policy=(ROOT/'runtime/ws2_policy_test.c').read_text()
    policy=policy.replace('static unsigned binds,',PREFIX+'\nstatic unsigned binds,',1)
    policy=policy.replace('(void)fd; (void)n; ++binds;',
        '(void)fd; (void)n; fixture_bad_bind+=((const struct sockaddr_in *)a)->sin_addr.s_addr!=htonl(INADDR_LOOPBACK); fixture_ports[fixture_port_count++]=ntohs(((const struct sockaddr_in *)a)->sin_port); ++binds;',1)
    policy=policy.replace('(void)fd; (void)buf; (void)flags; (void)a; (void)al; ++sends;',
        '(void)fd; (void)buf; (void)flags; (void)al; fixture_bad_send+=((const struct sockaddr_in *)a)->sin_addr.s_addr!=htonl(INADDR_LOOPBACK); fixture_send_ports[fixture_send_count++]=ntohs(((const struct sockaddr_in *)a)->sin_port); ++sends;',1)
    policy=policy.replace('memcpy(out, &completion_errno, sizeof completion_errno);',
        'if(level==SOL_SOCKET && name==SO_BROADCAST) {int one=1;memcpy(out,&one,4);*n=4;return 0;}\n    memcpy(out, &completion_errno, sizeof completion_errno);',1)
    policy=policy.replace('ip->sin_addr.s_addr = htonl(INADDR_LOOPBACK); ip->sin_port = htons(9999);',
        'ip->sin_addr.s_addr = htonl(fixture_name_ip); ip->sin_port = htons(fixture_name_port);',1)
    policy=policy.replace('#include WS2_C','#define listen fixture_listen\n#include WS2_C',1)
    policy=policy.replace('int main(void)', 'int policy_main_unused(void)')+MAIN
    source=(ROOT/'runtime/ws2_32.c').read_text()
    variants={'normal':source,
        'disabled':source.replace('if (lan_loopback()) a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);','if (0) a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);'),
        'sender':source.replace('sender.sin_addr.s_addr = lan_loopback() ? htonl(INADDR_LOOPBACK) : INADDR_ANY;', 'sender.sin_addr.s_addr = INADDR_ANY;'),
        'game-peer':source.replace('if(port==3658) a->sin_port=htons(3659);','if(port==3658) a->sin_port=htons(3658);'),
        'fanout':source.replace('local.sin_port = htons(10001);','local.sin_port = htons(9999);'),
        'listen':source.replace('if (lan_loopback()) {\n                struct sockaddr_in bound;', 'if (0) {\n                struct sockaddr_in bound;')}
    with tempfile.TemporaryDirectory(prefix='ws2-guarded-') as td:
        tmp=Path(td);unit=tmp/'test.c';unit.write_text(policy)
        for name,text in variants.items():
            variant=tmp/(name+'.c');variant.write_text(text);exe=tmp/name
            p=subprocess.run(['clang','-O1','-Iruntime','-Irecomp','-Irecomp/out','-DWS2_C="'+str(variant)+'"',str(unit),'-o',str(exe)],cwd=ROOT,capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2000:]
            p=subprocess.run([str(exe)],capture_output=True,text=True)
            print(name,p.stdout.strip(),flush=True)
            assert p.returncode==(0 if name=='normal' else 1),(name,p.stdout,p.stderr)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
