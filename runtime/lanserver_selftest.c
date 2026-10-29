// lanserver_selftest.c - BOOT_DIAG_LANSERVER, the in-game proof of the LAN host's server.dll (server_loader.c).
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "host.h"
#include "server_loader.h"
#include "lan_diag.h"

// BOOT_DIAG_LANSERVER=1 (diagnostic runs only; runtime/lanserver_check.sh): hosts and stops the LAN lobby server twice
// from the main loop, through the game's own code: the host path 00741E20 (thiscall, one argument: the name; it loads
// "server", resolves the three exports and calls StartServer with the game's four-argument cdecl frame) and the
// teardown 007361A0 (StopServer, FreeLibrary). Around them, through the game's own IAT cells as the game calls them:
// one extra LoadLibraryA reference to read the exports and IsServerRunning before the start and after the stop (so the
                                                                                                                      
// Every result is a `lanserver:` line on stderr; the check reads them.
extern uint32_t ws2_local_ip;
extern int ws2_trace, ws2_trace_listening, ws2_trace_accepts;
extern uint16_t ws2_trace_port, ws2_trace_peers[64];
int threads_module_running(uint32_t lo, uint32_t hi, uint32_t *start);
#define IAT_FreeLibrary 0x00890090u
#define IAT_GetProcAddress 0x00890094u
#define IAT_LoadLibraryA 0x00890098u
#define STR_server 0x008B4B5Cu                 // the game's own "server", "StartServer", "StopServer", "IsServerRunning"
static const uint32_t export_names[3] = {0x008B4B50u, 0x008B4B44u, 0x008B4B34u};
static uint64_t now_ms(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW) / 1000000; }
static uint32_t guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    uint32_t esp = c->esp, r = call_guest(c, fn, n, a);
    c->esp = esp;
    return r;
}
                                                                                
// wrapper as the menu, without constructing hidden FEng screens or faking packets.
static void discovery_pass(Cpu *outer) {
    static int on = -1;
    static uint64_t next;
    static uint32_t object, snapshot, kind;
    if (on < 0) {
        on = getenv("BOOT_DIAG_LANDISCOVERY") && atoi(getenv("BOOT_DIAG_LANDISCOVERY"));
        next = now_ms() + 8000;
    }
    if (!on || now_ms() < next) return;
    next = now_ms() + 3000;
    Cpu local = *outer;
    if (!object) {
        snapshot = galloc(2048);
        G[snapshot] = 0;
        // NetConnStartup is idempotently skipped if already open (same selector as MainLAN).
        uint32_t a[3] = {0x6F70656Eu, 0, 0};
        if (guest(&local, 0x0083CD40u, 3, a) != 1) {
            uint32_t empty[1] = {snapshot};
            guest(&local, 0x0083CCC0u, 1, empty);
        }
        // ServerSelect uses this region selector; null defaults to object+4,
        // an empty string which the discovery decoder matches literally.
        kind = guest(&local, 0x0064A3D0u, 0, NULL);
        object = guest(&local, 0x0083ED50u, 0, NULL);
        fprintf(stderr, "landiscovery: object %08X\n", object);
        if (!object || !kind) { on = 0; return; }
        fprintf(stderr, "landiscovery: game kind %.31s\n", (char *)G + kind);
    }
    uint32_t a[4] = {object, kind, snapshot, 2048};
    memset(G + snapshot, 0, 2048);
    uint32_t rows = guest(&local, 0x0083ECF0u, 4, a);
    fprintf(stderr, "landiscovery: cache rows %u [%.*s]\n", rows, 2047, (char *)G + snapshot);
}
// Read-only UI/lobby observation; never drives the game state machine.
static void original_lan_pass(Cpu *outer) {
    static uint64_t next;
    static char previous[256], old_package[96], old_hosts[2048];
    static uint32_t snapshot, kind;
    if(!lan_diag_on() || now_ms()<next)return;
    next=now_ms()+250;
    uint32_t eng=M32(0x0091CADCu),fe=eng?M32(eng):0;
    if(fe==0xFFFFFF28u)fe=0;
    const char *package="none";
    for(uint32_t p=fe?M32(fe+0xE4):0,n=0;p && n<64;p=M32(p+8),++n)
        if(M32(p+0x20)){package=(char *)G+M32(p+0xC);break;}
    if(strcmp(old_package,package)) {
        snprintf(old_package,sizeof old_package,"%s",package);
        fprintf(stderr,"lan-ui: package %s\n",old_package);
    }
    uint32_t core=M32(0x009B41C4),api=core?M32(core+0x38):0;
    if(api) {
        char line[256];
        snprintf(line,sizeof line,"auth=%d persona=%.15s room-id=%d room=%.35s",
            M32(api+8)==0x61757468,(char *)G+api+0x15A8,(int32_t)M32(api+0x17E4),(char *)G+api+0x17EC);
        if(strcmp(line,previous)){snprintf(previous,sizeof previous,"%s",line);fprintf(stderr,"lan-ui: lobby %s\n",line);}
    }
    // Only inspect discovery already opened by ServerSelect. No initialization,
    // hosting, selection, authentication or room command is issued here.
    uint32_t cache=M32(0x009C3878);
    if(cache && !strcmp(package,"UI_PC_LAN_ServerSelect.fng")) {
        Cpu local=*outer;
        if(!snapshot){snapshot=galloc(2048);kind=guest(&local,0x0064A3D0,0,NULL);}
        memset(G+snapshot,0,2048);
        uint32_t a[6]={cache,kind,0,snapshot,2048,0};
        // Decoder's empty protocol filter is a valid guest string, not NULL.
        a[2]=snapshot+2047;
        uint32_t rows=guest(&local,0x0084CDE0,6,a);
        if(strcmp(old_hosts,(char *)G+snapshot)) {
            snprintf(old_hosts,sizeof old_hosts,"%s",(char *)G+snapshot);
            fprintf(stderr,"lan-ui: hosts rows %u [%.*s]\n",rows,2047,old_hosts);
        }
    }
}
void lanserver_selftest_pass(Cpu *c) {
    original_lan_pass(c);
    discovery_pass(c);
    static int on = -1, step, cycle, fd = -1, accepts0, mine;
    static uint64_t hold_until;
    static uint64_t t;
    static uint32_t buf, h, exports[3];
    if (on < 0) {
        on = getenv("BOOT_DIAG_LANSERVER") && atoi(getenv("BOOT_DIAG_LANSERVER"));
        ws2_trace = on;
        t = now_ms();
    }
    if (!on || step < 0) return;
    uint32_t start = 0;
    int live = threads_module_running(SERVER_BASE, SERVER_RESERVED_END, &start);
    if (step == 0) {                                    // let the front end come up first
        if (now_ms() - t < 8000) return;
        uint32_t size = 64;
        buf = guest(c, 0x00652AD0u, 1, &size);          // the game's malloc
        strcpy((char *)G + buf, "LanHost");
        strcpy((char *)G + buf + 16, "NoSuchExport");
        step = 1;
    }
    if (step == 1) {                                    // load, exports, IsServerRunning, start through 00741E20
        fprintf(stderr, "lanserver: cycle %d\n", cycle + 1);
        uint32_t a[2] = {STR_server};
        h = guest(c, M32(IAT_LoadLibraryA), 1, a);
        fprintf(stderr, "lanserver: load %08X\n", h);
        if (!h) { fprintf(stderr, "lanserver: FAIL load (error %u)\n", last_error); step = -1; return; }
        for (int i = 0; i < 3; ++i) {
            a[0] = h; a[1] = export_names[i];
            exports[i] = guest(c, M32(IAT_GetProcAddress), 2, a);
            fprintf(stderr, "lanserver: export %s %08X\n", (char *)G + export_names[i], exports[i]);
        }
        a[0] = h; a[1] = buf + 16;
        uint32_t bad = guest(c, M32(IAT_GetProcAddress), 2, a);
        fprintf(stderr, "lanserver: export %s %08X error %u\n", (char *)G + buf + 16, bad, last_error);
        if (!exports[2]) { fprintf(stderr, "lanserver: FAIL exports\n"); step = -1; return; }
        fprintf(stderr, "lanserver: running before start %u\n", guest(c, exports[2], 0, NULL) & 0xFF);
        a[0] = buf;
        c->ecx = 0;                                     // the name-entry screen; only passed on to 007361A0
        uint32_t ok = guest(c, 0x00741E20u, 1, a) & 0xFF;
        fprintf(stderr, "lanserver: host start %u (module %08X, exports %08X %08X %08X)\n", ok, M32(0x009B08E8u),
                M32(0x009B08ECu), M32(0x009B08F0u), M32(0x009B08F4u));
        a[0] = h;
        guest(c, M32(IAT_FreeLibrary), 1, a);           // this test's reference; the game's stays
        fprintf(stderr, "lanserver: running after start %u\n", guest(c, exports[2], 0, NULL) & 0xFF);
        accepts0 = ws2_trace_accepts;
        t = now_ms(); step = ok ? 2 : 4;
        return;
    }
    if (step == 2) {                                                                                               
        if (!(live && ws2_trace_listening > 0)) {
            if (now_ms() - t < 10000) return;
            fprintf(stderr, "lanserver: FAIL no listening thread (threads %d, listening %d)\n", live, ws2_trace_listening);
            step = 4; return;
        }
        fprintf(stderr, "lanserver: worker %08X running, listening on port %u\n", start, ws2_trace_port);
        fd = socket(AF_INET, SOCK_STREAM, 0);
        fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
        uint32_t target = ws2_local_ip == 0x7F000002 ? 0x7F000001 : ws2_local_ip;
        struct sockaddr_in sa = {.sin_family = AF_INET, .sin_port = htons(ws2_trace_port), .sin_addr.s_addr = htonl(target)};
        char target_ip[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &sa.sin_addr, target_ip, sizeof target_ip);
        int r = connect(fd, (struct sockaddr *)&sa, sizeof sa), e = r && errno != EINPROGRESS ? errno : 0;
        socklen_t al = sizeof sa;
        getsockname(fd, (struct sockaddr *)&sa, &al);
        fprintf(stderr, "lanserver: host connect from port %u to %s:%u -> %d\n", ntohs(sa.sin_port), target_ip, ws2_trace_port, e);
        mine = 0;
        t = now_ms(); step = 3;
        return;
    }
    if (step == 3) {                                    // the server's accept of this connection (it connects to itself too)
        struct sockaddr_in sa;
        socklen_t al = sizeof sa;
        getsockname(fd, (struct sockaddr *)&sa, &al);
        for (; accepts0 < ws2_trace_accepts; ++accepts0) mine |= ws2_trace_peers[accepts0 & 63] == ntohs(sa.sin_port);
        if (!mine && now_ms() - t < 10000) return;
        fprintf(stderr, "lanserver: accepted host connection %d\n", mine);
        const char *hold = getenv("BOOT_DIAG_LANSERVER_HOLD");
        unsigned seconds = hold ? (unsigned)strtoul(hold, NULL, 10) : 0;
        if (seconds > 300) seconds = 300;
        hold_until = now_ms() + (uint64_t)seconds * 1000;
        if (seconds) fprintf(stderr, "lanserver: holding lobby %u seconds\n", seconds);
        step = 4;
    }
    if (step == 4 && now_ms() < hold_until) return;
    if (step == 4) {                                    // stop through 007361A0, holding a reference to look after it
        uint32_t a[1] = {STR_server};
        h = guest(c, M32(IAT_LoadLibraryA), 1, a);
        guest(c, 0x007361A0u, 0, NULL);
        fprintf(stderr, "lanserver: game teardown (module %08X, exports %08X %08X %08X)\n", M32(0x009B08E8u),
                M32(0x009B08ECu), M32(0x009B08F0u), M32(0x009B08F4u));
        fprintf(stderr, "lanserver: running after stop %u\n", h ? guest(c, exports[2], 0, NULL) & 0xFF : 0xFFu);
        t = now_ms(); step = 5;
        return;
    }
    if (step == 5) {                                                                                       
        if ((live || ws2_trace_listening > 0) && now_ms() - t < 5000) return;
        fprintf(stderr, "lanserver: after stop workers %d, listening %d\n", live, ws2_trace_listening);
        extern const char *server_import_names[256];
        extern uint32_t server_import_calls[256];
        for (int i = 0; i < 256; ++i)
            if (server_import_calls[i]) fprintf(stderr, "lanserver: import %u %s\n", server_import_calls[i], server_import_names[i]);
        uint32_t a[1] = {h};
        uint32_t freed = h ? guest(c, M32(IAT_FreeLibrary), 1, a) : 0;
        fprintf(stderr, "lanserver: unload %u, module %08X\n", freed, server_handle());
        if (fd >= 0) { close(fd); fd = -1; }
        if (++cycle < 2) { step = 1; return; }
        fprintf(stderr, "lanserver: done\n");
        step = -1;
    }
}
