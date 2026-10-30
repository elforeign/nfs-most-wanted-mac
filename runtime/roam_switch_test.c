// Production game hook with fake socket/guest/menu backends; no game or network execution.
#include "host.h"
#include "settings.h"
#include "roam_online.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
static int fake_socket(int domain, int type, int protocol);
static int fake_bind(int fd, const struct sockaddr *a, socklen_t n);
static int fake_connect(int fd, const struct sockaddr *a, socklen_t n);
static int fake_close(int fd);
static int fake_fcntl(int fd, int cmd, ...);
static ssize_t fake_send(int fd, const void *b, size_t n, int flags);
static ssize_t fake_recv(int fd, void *b, size_t n, int flags);
#define socket fake_socket
#define bind fake_bind
#define connect fake_connect
#define close fake_close
#define fcntl fake_fcntl
#define send fake_send
#define recv fake_recv
static int distance_calls;
#include "roam_game.c"
#undef socket
#undef bind
#undef connect
#undef close
#undef fcntl
#undef send
#undef recv
uint8_t *G;
int diag_on;
const char *diag_dir;
volatile uint64_t perf_nframes;
static int checks, failures, opens, closes, original_calls, mainloop_calls, target_calls, command, guest_calls, notices;
static int connect_error, bye_packets, endpoint_events;
uint64_t host_ticks(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec; }
static uint32_t wanted_room;
static Setting *registered[16]; static int nregistered;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures < 5) printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)
void diag_event(const char *fmt, ...) {
    char line[512]; va_list ap; va_start(ap, fmt); vsnprintf(line, sizeof line, fmt, ap); va_end(ap);
    if (strstr(line, "127.0.0.1")) ++endpoint_events;
}
void overlay_notice(const char *s) { (void)s; ++notices; }
uint32_t galloc(uint32_t n) { (void)n; ++guest_calls; return 0x10000; }
static int budget_mode, factory_fails, factory_calls, kill_calls;
static int appearance_mode;
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    (void)c; (void)n; (void)args; ++guest_calls;
    if (appearance_mode) {
        if (fn == 0x00455FD0u) return args[1] == 123 ? 0xb000 : 0;
        if (fn == 0x0056F1B0u) { memset(G + c->ecx, 0xff, 0x198); return 0; }
        if (fn == 0x00747C00u) return args[0] == 10 ? 0xb000 : 0;
    }
    if (budget_mode && fn == 0x0041CB10u) {
        ++factory_calls;
        if (factory_fails) return 0;
        uint32_t sim = 0x40000 + (uint32_t)factory_calls * 0x100;
        M32(sim) = 0x60000; M32(0x60008) = 0xdead;
        M32(0x8000 + 4 * M32(0x0092CD84u)) = sim + 0x80; ++M32(0x0092CD84u);
        return sim;
    }
    if (budget_mode && fn == 0xdead) {
        ++kill_calls;
        for (unsigned i = 0; i < M32(0x0092CD84u); ++i) if (M32(0x8000 + 4*i) == c->ecx + 0x80) M32(0x8000 + 4*i) = 0;
    }
    return 0;
}
void f_0075AAD0_orig(Cpu *c) { (void)c; ++original_calls; }
void f_00663D30_orig(Cpu *c) { (void)c; ++mainloop_calls; }
void f_0069B740_orig(Cpu *c) { (void)c; }
int user32_ghost_buttons(void) { return 0; }
int portmenu_is_open(void) { return 0; }
int osk_visible(void) { return 0; }
void settings_add(Setting *s) { if (nregistered < 16) registered[nregistered++] = s; }
const char *settings_text(const char *id) { (void)id; return ""; }
void roam_online_host(const char *s) { (void)s; }
void roam_online_join(const char *s) { (void)s; }
void roam_online_leave(void) {}
void roam_online_host_code(char *s, int n) { snprintf(s, (size_t)n, ""); }
void roam_online_status(char *s, int n) { snprintf(s, (size_t)n, "Off"); }
void roam_online_players(int w, int p, int e) { (void)w; (void)p; (void)e; }
int roam_online_target(char host[64], int *port, uint32_t *room) {
    ++target_calls; int action = command; command = 0;
    if (action > 0) { snprintf(host, 64, "127.0.0.1"); *port = 47800; *room = wanted_room; }
    return action;
}
static int fake_socket(int domain, int type, int protocol) { CHECK(domain == AF_INET && type == SOCK_DGRAM && !protocol); return 100 + opens++; }
static int fake_bind(int fd, const struct sockaddr *a, socklen_t n) {
    (void)fd; CHECK(n == sizeof(struct sockaddr_in) && ntohl(((const struct sockaddr_in *)a)->sin_addr.s_addr) == INADDR_LOOPBACK); return 0;
}
static int fake_connect(int fd, const struct sockaddr *a, socklen_t n) {
    (void)fd; CHECK(n == sizeof(struct sockaddr_in) && ntohl(((const struct sockaddr_in *)a)->sin_addr.s_addr) == INADDR_LOOPBACK);
    if (connect_error) { errno = EHOSTUNREACH; return -1; } return 0;
}
static int fake_close(int fd) { (void)fd; ++closes; return 0; }
static int fake_fcntl(int fd, int cmd, ...) { (void)fd; (void)cmd; return 0; }
static ssize_t fake_send(int fd, const void *b, size_t n, int flags) {
    (void)fd; (void)flags; int type; uint16_t id, seq;
    if (!roam_hdr_get(b, (int)n, &type, &id, &seq) && type == ROAM_BYE) ++bye_packets;
    return (ssize_t)n;
}
static ssize_t fake_recv(int fd, void *b, size_t n, int flags) { (void)fd; (void)b; (void)n; (void)flags; errno = EAGAIN; return -1; }
int main(void) {
    G = calloc(1, 16 * 1024 * 1024); if (!G) return 2;
    Cpu c = {0}; unsetenv("NFS_ROAM"); unsetenv("NFS_ROAM_GHOST"); unsetenv("NFS_ROAM_CLONES");
    CHECK(nregistered == 8);
    for (int i = 0; i < nregistered; ++i) CHECK(registered[i]->category == SET_ONLINE);
    f_0075AAD0(&c); int before = target_calls;
    for (int i = 0; i < 100; ++i) f_0075AAD0(&c);
    CHECK(opens == 0 && guest_calls == 0 && original_calls == 101 && target_calls == before);
    wanted_room = 1234; command = 1; roam_game_wake(); f_0075AAD0(&c);
    CHECK(opens == 1 && roam_fd == 100 && roam.room == 1234 && atomic_load(&roam_state) == 1);
    CHECK(atomic_load(&roam_online_active) == 1);
    roam_car[0].used = 1; roam_car[0].id = 55; roam_ghosts = 1;
    roam.view.r[0].used = 1; roam.view.r[0].id = 55;
    wanted_room = 4567; command = 1; roam_game_wake(); f_0075AAD0(&c);
    CHECK(opens == 2 && closes == 1 && roam.room == 4567 && !roam_car[0].used && !roam_ghosts && !roam.view.r[0].used);
    // Front-end Leave: only MainLoop runs; World_Service is never called again here.
    int world_before = original_calls, bye_before = bye_packets;
    command = -1; roam_game_wake(); f_00663D30(&c);
    CHECK(roam_fd == -1 && closes == 2 && atomic_load(&roam_state) == -1);
    CHECK(atomic_load(&roam_online_active) == 0);
    CHECK(roam_online_limit(120, atomic_load(&roam_online_active)) == 120);
    CHECK(original_calls == world_before && mainloop_calls == 1 && bye_packets == bye_before + 1);
    for (int i = 0; i < 100; ++i) f_00663D30(&c);
    CHECK(closes == 2 && original_calls == world_before);
    before = target_calls; for (int i = 0; i < 100; ++i) f_0075AAD0(&c);
    CHECK(target_calls == before && opens == 2 && guest_calls == 0);
    // The legacy environment path still selects its room/name/test switches.
    setenv("NFS_ROAM", "127.0.0.1:47800", 1); setenv("NFS_ROAM_ROOM", "42", 1);
    setenv("NFS_ROAM_NAME", "Tester", 1); setenv("NFS_ROAM_GHOST", "1", 1); setenv("NFS_ROAM_CLONES", "3", 1);
    roam_env_checked = 0; atomic_store(&roam_state, 0); f_0075AAD0(&c);
    CHECK(opens == 3 && roam.room == 42 && !strcmp(roam.name, "Tester") && roam_ghost_mode && roam_clones == 3);
    CHECK(!endpoint_events);
    roam.welcomed = 1; roam_last_rx = roam_ms() - 6000; f_0075AAD0(&c); CHECK(!roam.welcomed);
    command = -1; roam_game_wake(); f_0075AAD0(&c); CHECK(closes == 3 && roam_fd == -1);
    // Inject snapshots after the network buffer to exercise the final pre-distance guard.
    memset(roam_car, 0, sizeof roam_car); roam_view_init(&roam.view); roam_clones = 1;
    roam_mem = 0x10000; M32(0x008EF998u) = 1; M32(0x009142D0u) = 0x1000; M32(0x1034) = 0x2000;
    RoamRemote *remote = &roam.view.r[0]; remote->used = 1; remote->id = 7; remote->n = 1;
    remote->last_rx = 1000;
    RoamState good = {.t=1000, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
    const float invalid[] = {NAN, INFINITY, -INFINITY, 1000001.0f, -1000001.0f};
    for (int field = 0; field < 6; ++field) for (unsigned j = 0; j < sizeof invalid / sizeof *invalid; ++j) {
        remote->s[0] = good;
        if (field < 3) remote->s[0].pos[field] = invalid[j]; else remote->s[0].vel[field-3] = invalid[j];
        distance_calls = 0; roam_cars(&c, 1000, 1); CHECK(distance_calls == 0);
    }
    for (int axis = 0; axis < 3; ++axis) {
        remote->s[0] = good; remote->s[0].vel[axis] = 10001.0f;
        distance_calls = 0; roam_cars(&c, 1000, 1); CHECK(distance_calls == 0);
    }
    remote->s[0] = good; distance_calls = 0; roam_cars(&c, 1000, 1); CHECK(distance_calls == 1);
    // Endpoint-bearing errors must not copy the input to ordinary stderr.
    CHECK(roam_open("127.0.0.1:bad", 0) < 0);
    connect_error = 1; CHECK(roam_open("127.0.0.1:47800", 0) < 0);
    printf("switch: %d checks, %d failures; sockets opened/closed=%d/%d, guest calls=%d\n", checks, failures, opens, closes, guest_calls);
    free(G); return failures ? 1 : 0;
}
