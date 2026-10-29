"""Offline proof of LAN harness fixes; no sockets or game.
Measures injected 384-byte packets and macOS SO_REUSEPORT's native boolean.
Measured: packet/boolean 5/0; overlap 5/2; boolean 5/1.
Option-order 16/0; late SO_REUSEPORT control 16/2.
Loopback guard 7/0; unsafe adapter 7/6; implicit ANY listen 7/2; all host calls fake.
The same production packet()/reuse_enabled() run against both broken controls.
"""
from pathlib import Path
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parent.parent
PREFIX = r'''
#include <arpa/inet.h>
#include <poll.h>
#include <sys/socket.h>
#include <string.h>
#include <stdint.h>
static int fixture_option = 512;
static int fixture_getsockopt(int fd, int level, int name, void *out, socklen_t *len) {
    (void)fd; (void)level; (void)name;
    memcpy(out, &fixture_option, sizeof fixture_option); *len = sizeof fixture_option; return 0;
}
static int fixture_poll(struct pollfd *p, nfds_t n, int ms) {
    (void)n; (void)ms; p[0].revents = POLLIN; return 1;
}
static ssize_t fixture_recvmsg(int fd, struct msghdr *msg, int flags) {
    (void)fd; (void)flags;
    // Exactly the zero-filled broadcast produced by broadcast(), with its tag.
    memset(msg->msg_iov[0].iov_base, 0, 384);
    uint32_t tag = 0xA201; memcpy(msg->msg_iov[0].iov_base, &tag, 4);
    struct sockaddr_in a = {.sin_family=AF_INET, .sin_port=htons(9999),
        .sin_addr.s_addr=htonl(0xC0A80102)};
    memcpy(msg->msg_name, &a, sizeof a); msg->msg_namelen=sizeof a; msg->msg_flags=0;
    return 384;
}
#define recvmsg fixture_recvmsg
'''
SUFFIX = r'''
#undef main
#undef check
int main(void) {
    G=calloc(1,0x1000000);
    socks[0]=(Sock){.fd=101,.type=2,.guest_nonblock=1};
    unsigned before=failures;
    check(packet(SOCK_BASE,0xA201,0xC0A80102), "384 byte packet retains source sockaddr");
    check(M32(0x7300)==16, "receive length lives outside payload");
    check(reuse_enabled(SOCK_BASE), "native boolean 512 is enabled");
    fixture_option=0;
    check(!reuse_enabled(SOCK_BASE), "native boolean 0 is disabled");
    printf("LAN FIXTURE %s: %u checks, %u failures\n", failures==before?"PASS":"FAIL",checks,failures);
    free(G); return failures!=before;
}
'''


def option_order(tmp):
    # Run the production dispatcher with the existing fake host sockets, recording
    # native option state at each bind. This also exercises literal Winsock opt 4.
    policy=(ROOT/'runtime/ws2_policy_test.c').read_text()
    policy=policy.replace('static unsigned binds,', 'static unsigned fixture_options, fixture_order_bad;\nstatic unsigned binds,',1)
    policy=policy.replace('(void)fd; (void)len;\n', '(void)fd; (void)len;\n    if (l==SOL_SOCKET && n==SO_REUSEADDR && *(const int *)p) fixture_options |= 1;\n    if (l==SOL_SOCKET && n==SO_REUSEPORT && *(const int *)p) fixture_options |= 2;\n',1)
    policy=policy.replace('(void)fd; (void)n; ++binds;', '(void)fd; (void)n; if (ntohs(((const struct sockaddr_in *)a)->sin_port)==9999 && fixture_options!=3) ++fixture_order_bad; ++binds;',1)
    policy=policy.replace('(void)af; (void)type; (void)proto; ++sockets;', '(void)af; (void)type; (void)proto; fixture_options=0; ++sockets;',1)
    policy=policy.replace('int main(void)', 'int policy_main_unused(void)')
    policy += r'''
int main(void) {
    G=calloc(1,0x1000000); unsetenv("NFS_LAN_COPY");
    for (unsigned copy=0;copy<2;++copy) {
        if(copy) setenv("NFS_LAN_COPY","1",1);
        uint32_t s=invoke("#23",3,2u,2u,0u);
        M32(0x6000)=1;
        check(invoke("#21",5,s,0xFFFFu,4u,0x6000u,4u)==0 && fixture_options==1,
            "literal Winsock SO_REUSEADDR reaches native pre-bind option");
        address("0.0.0.0",9999);
        check(invoke("#2",3,s,0x2000u,16u)==0,"bind completes");
        check(!fixture_order_bad,"both native reuse options set BEFORE every 9999 bind");
        invoke("#3",1,s);
    }
    printf("LAN ORDER %s: %u checks, %u failures\n", failures?"FAIL":"PASS",checks,failures);
    free(G); return failures!=0;
}
'''
    unit=tmp/'order.c';unit.write_text(policy)
    production=(ROOT/'runtime/ws2_32.c').read_text()
    line='                if (discovery && setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof one)) RET((uint32_t)fail(errno), 3);\n'
    assert production.count(line)==1
    late=production.replace(line,'').replace('                r = bind(fd, (struct sockaddr *)&a, sizeof a);','                r = bind(fd, (struct sockaddr *)&a, sizeof a);\n'+line,1)
    for name, text in [('normal',production),('late-reuseport',late)]:
        variant=tmp/(name+'-ws.c');variant.write_text(text)
        exe=tmp/(name+'-order')
        cmd=['clang','-O1','-Iruntime','-Irecomp','-Irecomp/out','-DWS2_C="'+str(variant)+'"',str(unit),'-o',str(exe)]
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True)
        assert p.returncode==0,p.stderr[-2000:]
        p=subprocess.run([str(exe)],capture_output=True,text=True)
        print(name,p.stdout.strip(),flush=True)
        assert p.returncode==(0 if name=='normal' else 1),(name,p.stdout,p.stderr)


def transport_guard(tmp):
    header=(ROOT/'runtime/ws2_lan_transport.h').read_text()
    code=r'''
#include <arpa/inet.h>
#include <sys/socket.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
static unsigned calls, unsafe;
static int kernel_bind(int fd,const struct sockaddr *a,socklen_t len) {
    (void)fd;(void)len;++calls;
    unsafe += ((const struct sockaddr_in *)a)->sin_addr.s_addr != htonl(INADDR_LOOPBACK);
    return 0;
}
static int kernel_listen(int fd,int backlog) {(void)fd;(void)backlog;++unsafe;return 0;}
static int kernel_connect(int fd,const struct sockaddr *a,socklen_t len) {return kernel_bind(fd,a,len);}
static ssize_t kernel_sendto(int fd,const void *p,size_t n,int flags,const struct sockaddr *a,socklen_t len) {
    (void)p;(void)flags;kernel_bind(fd,a,len);return n;
}
#define listen kernel_listen
#define bind kernel_bind
#define connect kernel_connect
#define sendto kernel_sendto
#include "HEADER"
int main(void) {
    unsetenv("NFS_LAN_REAL");
    unsigned checks=0,failed=0;
#define GUARD(x) do {++checks;failed+=!(x);} while(0)
    uint32_t ips[]={0,0xC0A80102,0xFFFFFFFF};
    for(unsigned i=0;i<3;++i) {
        struct sockaddr_in a={.sin_family=AF_INET,.sin_addr.s_addr=htonl(ips[i])};
        GUARD(lan_test_bind(100,(const struct sockaddr *)&a,sizeof a)==0);
    }
    struct sockaddr_in a={.sin_family=AF_INET,.sin_addr.s_addr=htonl(0xFFFFFFFF)};
    GUARD(lan_test_connect(100,(const struct sockaddr *)&a,sizeof a)==0);
    GUARD(lan_test_sendto(100,"x",1,0,(const struct sockaddr *)&a,sizeof a)==1);
    GUARD(lan_test_listen(-1,1)==-1 && errno==EACCES);
    GUARD(calls==5 && !unsafe && !lan_nonloopback_binds);
    printf("LAN GUARD %s: %u checks, %u failures, kernel unsafe=%u\n",failed?"FAIL":"PASS",checks,failed,unsafe);
    return failed!=0;
}
'''
    for name,text in [('normal',header),('unsafe-adapter',header.replace('if (!lan_real()) out.sin_addr.s_addr = htonl(INADDR_LOOPBACK);','if (0) out.sin_addr.s_addr = htonl(INADDR_LOOPBACK);')), ('unsafe-listen',header.replace('if (!lan_real()) {','if (0) {'))]:
        path=tmp/(name+'.h');path.write_text(text)
        unit=tmp/(name+'-guard.c');unit.write_text(code.replace('HEADER',str(path)))
        exe=tmp/(name+'-guard')
        p=subprocess.run(['clang','-O1',str(unit),'-o',str(exe)],capture_output=True,text=True)
        assert p.returncode==0,p.stderr[-2000:]
        p=subprocess.run([str(exe)],capture_output=True,text=True)
        print(name,p.stdout.strip(),flush=True)
        assert p.returncode==(0 if name=='normal' else 1),(name,p.stdout,p.stderr)


def main():
    source=(ROOT/'runtime/ws2_lan_test.c').read_text()
    variants={
        'normal':source,
        'overlap':source.replace('M32(0x7300) = 16;', 'M32(0x7100) = 16;').replace('0x7200u, 0x7300u);','0x7200u, 0x7100u);'),
        'boolean':source.replace('reuse != 0', 'reuse == 1'),
    }
    with tempfile.TemporaryDirectory(prefix='lan-fixture-') as td:
        tmp=Path(td)
        transport_guard(tmp)
        option_order(tmp)
        for name, text in variants.items():
            # Instrument only harness host calls. Production recv_packet sees
            # injected recvmsg via the macro; policy_test later undefines it.
            text=text.replace('getsockopt(sock_fd(s)', 'fixture_getsockopt(sock_fd(s)')
            text=text.replace('poll(&p, 1, 50)', 'fixture_poll(&p, 1, 50)')
            text=text.replace('int main(void)', 'int socket_main_unused(void)')
            harness=tmp/(name+'.c'); harness.write_text(PREFIX+text+SUFFIX)
            exe=tmp/name
            p=subprocess.run(['clang','-O1','-Iruntime','-Irecomp','-Irecomp/out',str(harness),'-o',str(exe)],cwd=ROOT,capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2000:]
            p=subprocess.run([str(exe)],capture_output=True,text=True)
            print(name,p.stdout.strip(),flush=True)
            assert p.returncode==(0 if name=='normal' else 1),(name,p.stdout,p.stderr)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
