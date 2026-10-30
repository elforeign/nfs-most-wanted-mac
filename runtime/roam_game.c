// roam_game.c - online free roam, game side: the local car out to the relay, friends' poses in (roam_core.h).
//
// NFS_ROAM=<relay ip:port> turns it on (NFS_ROAM_ROOM=<n>, default 1; NFS_ROAM_NAME=<name>, default career name;
// NFS_ROAM_CLONES=<n>, test only: n cars per friend for frame-cost measurements; NFS_ROAM_GHOST=1 starts with ghost
// mode on). Ghost mode toggles with D-pad Down (pad 0) or G while driving online, or in F10 > Online > Ghost mode.
// Unset, MainLoop and World_Service only test session state: no socket, no thread.
//
// Per frame, after World_Service 0075AAD0 (the world/sim step MainLoop 00663D30 runs before Main_DisplayFrame):
// drain the socket into the client session, send HELLO until WELCOME, then the local car's state at 20 Hz.
// The local car: SndCamera::m_PLayerCars[0] 009142D0 (NumPlayers 008EF998) -> EAXCar +0x34 EAX_CarState, which
// CarSoundConn::UpdateState 004B15E0 fills each frame: the world body matrix copied to +0x10 (004B1655: ecx =
// state + 0x10, push body, call 00445090 bMatrixCopy) - rotation rows +0x10/+0x20/+0x30, position row +0x40 - and
// the body's velocity at +0x50.
// BOOT_DIAG: <diag>/roam.log, one `L t x y z` line per frame for the local car and `R id t x y z qx qy qz qw` per
// remote (t = this process's monotonic ms, which every process on one Mac shares), and `roam:` events once a second.
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <strings.h>
#include <time.h>
#include <unistd.h>
#include "host.h"
#include "roam_core.h"
#include "settings.h"
#include "roam_online.h"
#include <stdatomic.h>

int user32_ghost_buttons(void);   // user32.m: bit 0 D-pad Down on pad 0, bit 1 the G key
int portmenu_is_open(void);
int osk_visible(void);

static atomic_int roam_state;   // 0 initial, 1 active, -1 off, 2 pending session change
atomic_int roam_online_active;
void roam_game_wake(void) { atomic_store(&roam_state, 2); }
static int roam_env_checked;
static uint32_t roam_started, roam_last_rx;
static int roam_fd = -1;
static RoamClient roam;
static uint32_t roam_last_hello, roam_last_state, roam_last_sec;
static uint32_t roam_tx, roam_rx;
static FILE *roam_log;
static RoamLook roam_local_look;
static uint32_t roam_last_look;
static int roam_clones = 1;     // NFS_ROAM_CLONES
static int roam_ghost_mode;     // the player's ghost mode (NFS_ROAM_GHOST=1 starts with it on)
static RoamToggle roam_toggle_pad, roam_toggle_key;
static unsigned roam_ghost_skips;   // collision pairs skipped for a ghost (diag summary)
static int roam_ghosts;         // friend cars with ghost set this frame (the collision hook's fast exit)

static uint32_t roam_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u);
}

static int roam_open(const char *target, uint32_t room_override) {
    const char *v = target ? target : getenv("NFS_ROAM");
    if (!v || !*v) return -1;
    char host[64];
    int port = ROAM_PORT;
    const char *colon = strrchr(v, ':');
    size_t n = colon ? (size_t)(colon - v) : strlen(v);
    if (n >= sizeof host) n = sizeof host - 1;
    memcpy(host, v, n); host[n] = 0;
    if (colon) port = atoi(colon + 1);
    struct sockaddr_in to = {0}, me = {0};
    to.sin_family = me.sin_family = AF_INET;
    to.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, host, &to.sin_addr) != 1 || port <= 0 || port > 65535) {
        fprintf(stderr, "roam: NFS_ROAM is not ip:port; online free roam off\n");
        return -1;
    }
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) { fprintf(stderr, "roam: socket: %s\n", strerror(errno)); return -1; }
    // A loopback relay gets a loopback source address: the socket never receives on another interface.
    me.sin_addr.s_addr = (ntohl(to.sin_addr.s_addr) >> 24) == 127 ? htonl(INADDR_LOOPBACK) : htonl(INADDR_ANY);
    if (bind(fd, (struct sockaddr *)&me, sizeof me) || connect(fd, (struct sockaddr *)&to, sizeof to)) {
        fprintf(stderr, "roam: bind/connect: %s\n", strerror(errno));
        close(fd);
        return -1;
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    const char *clones = getenv("NFS_ROAM_CLONES");     // test switch: n cars per friend, 5 m apart (frame-cost A/B)
    if (clones && atoi(clones) > 0) roam_clones = atoi(clones) > 16 ? 16 : atoi(clones);
    const char *ghost = getenv("NFS_ROAM_GHOST");
    if (ghost && *ghost == '1') roam_ghost_mode = 1;
    const char *room = getenv("NFS_ROAM_ROOM"), *name = getenv("NFS_ROAM_NAME");
    char profile_name[16] = "player";
    uint32_t fe = M32(0x0091CF90u); // FE database; profile pointer at +0x10 (extratrax.c).
    uint32_t profile = fe ? M32(fe + 0x10) : 0;
    if (profile && profile < 0x10000000u) snprintf(profile_name, sizeof profile_name, "%.15s", (const char *)G + profile);
    roam_client_init(&roam, target ? room_override : room ? (uint32_t)strtoul(room, NULL, 0) : 1u,
                     name && *name ? name : *profile_name ? profile_name : "player");
    roam_started = roam_last_rx = roam_ms();
    if (diag_on && diag_dir) {
        char path[1024];
        snprintf(path, sizeof path, "%s/roam.log", diag_dir);
        roam_log = fopen(path, "w");
    }
    if (diag_on) diag_event("roam: on, relay %s:%d room %u name %s (wire v%d)", host, port, roam.room, roam.name, ROAM_VERSION);
    return fd;
}

static void roam_send(const uint8_t *b, int n) {
    if (send(roam_fd, b, (size_t)n, 0) == n) roam_tx++;
}

// Rotation matrix rows (r[i][j] = row i, column j) to a unit quaternion (x, y, z, w); roam_quat_rows is its inverse.
static void roam_rows_quat(const float r[3][4], float q[4]) {
    float tr = r[0][0] + r[1][1] + r[2][2];
    if (tr > 0) {
        float s = sqrtf(tr + 1) * 2;
        q[3] = s / 4; q[0] = (r[2][1] - r[1][2]) / s; q[1] = (r[0][2] - r[2][0]) / s; q[2] = (r[1][0] - r[0][1]) / s;
    } else if (r[0][0] > r[1][1] && r[0][0] > r[2][2]) {
        float s = sqrtf(1 + r[0][0] - r[1][1] - r[2][2]) * 2;
        q[3] = (r[2][1] - r[1][2]) / s; q[0] = s / 4; q[1] = (r[0][1] + r[1][0]) / s; q[2] = (r[0][2] + r[2][0]) / s;
    } else if (r[1][1] > r[2][2]) {
        float s = sqrtf(1 + r[1][1] - r[0][0] - r[2][2]) * 2;
        q[3] = (r[0][2] - r[2][0]) / s; q[0] = (r[0][1] + r[1][0]) / s; q[1] = s / 4; q[2] = (r[1][2] + r[2][1]) / s;
    } else {
        float s = sqrtf(1 + r[2][2] - r[0][0] - r[1][1]) * 2;
        q[3] = (r[1][0] - r[0][1]) / s; q[0] = (r[0][2] + r[2][0]) / s; q[1] = (r[1][2] + r[2][1]) / s; q[2] = s / 4;
    }
    float l = sqrtf(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
    if (!(l > 0)) { q[0] = q[1] = q[2] = 0; q[3] = 1; return; }
    for (int i = 0; i < 4; ++i) q[i] /= l;
}

// The local car's state, 0 when there is none (front end, loading).
static int roam_local(RoamState *s, uint32_t now) {
    if ((int32_t)M32(0x008EF998u) < 1) return 0;
    uint32_t car = M32(0x009142D0u), st = car ? M32(car + 0x34) : 0;
    if (!st) return 0;
    float m[4][4];
    memcpy(m, G + st + 0x10, sizeof m);
    memcpy(s->vel, G + st + 0x50, sizeof s->vel);
    for (int i = 0; i < 3; ++i) {
        s->pos[i] = m[3][i];
        if (!isfinite(s->pos[i]) || !isfinite(s->vel[i]) || fabsf(s->pos[i]) > 1e5f) return 0;
    }
    if (s->pos[0] == 0 && s->pos[1] == 0 && s->pos[2] == 0) return 0;   // state not filled yet (first frames)
    float q[4];
    roam_rows_quat((const float (*)[4])m, q);
    for (int i = 0; i < 4; ++i) s->q[i] = (int16_t)lrintf(q[i] * 32767.0f);
    s->t = now;
    s->flags = ROAM_FLAG_CAR | (roam_ghost_mode ? ROAM_FLAG_GHOST : 0);
    s->look = roam_local_look.rev;
    return 1;
}

// ---- Friends' cars (driver class 6 -> RBRemote body, no AI brain; pose written after each sim step) ----
// VehicleParams as GRacerInfo::CreateVehicle 005E9004-005E90B7 builds it: +0 VehicleParams::TypeName(), +4 0xA6B47FAC,
// +8 &params, +0x10 driver class, +0x14 pvehicle key, +0x18 &direction, +0x1c &position, +0x20 customization,
// +0x24 owner (0 = no cache eviction, 00687710 tests it), +0x28 performance, +0x2c flags. The factory 0041CB10
// returns the ISimable (PVehicle+0x2c, vtable 008AA940: +8 Kill); IVehicle = ISimable+0x80, IRigidBody = [ISimable+0x4c].
#define ROAM_CARS ROAM_CAR_BUDGET
typedef struct { uint16_t id; uint8_t used, clone, ghost; uint16_t look_rev; uint32_t sim; } RoamCar;
static RoamCar roam_car[ROAM_CARS];
static uint32_t roam_mem;
static unsigned roam_spawned, roam_spawn_fail, roam_killed, roam_lost;
typedef struct { uint32_t since, delay; } RoamWait;
static RoamWait roam_retry_wait, roam_lifecycle_wait;
static int roam_waiting(RoamWait *wait, uint32_t now) {
    if ((uint32_t)(now - wait->since) >= wait->delay) wait->delay = 0;
    return wait->delay != 0;
}

static uint32_t roam_call(Cpu *c, uint32_t self, uint32_t fn, int n, const uint32_t *args) {
    Cpu q = *c;
    q.esp = roam_mem + 0x40000;
    q.ecx = self;
    return call_guest(&q, fn, n, args);
}
static int roam_vehicle_listed(uint32_t ivehicle) {   // vehicle list 0 (DebugWorld 0075546C)
    uint32_t base = M32(0x0092CD7Cu), n = M32(0x0092CD84u);
    for (uint32_t i = 0; base && i < n && i < 256; ++i)
        if (M32(base + 4 * i) == ivehicle) return 1;
    return 0;
}
static uint32_t roam_local_vehicle(void) {             // the human-driven IVehicle (driver class 0)
    uint32_t base = M32(0x0092CD7Cu), n = M32(0x0092CD84u);
    for (uint32_t i = 0; base && i < n && i < 256; ++i) {
        uint32_t v = M32(base + 4 * i);
        if (v && M32(v + 0x94) == 0) return v;
    }
    return 0;
}
// Rotation rows (as EAX_CarState holds them) from a unit quaternion: the inverse of roam_rows_quat.
static void roam_quat_rows4(const float q[4], const float pos[3], float m[4][4]) {
    float x = q[0], y = q[1], z = q[2], w = q[3];
    m[0][0] = 1 - 2 * (y * y + z * z); m[0][1] = 2 * (x * y - z * w);     m[0][2] = 2 * (x * z + y * w);
    m[1][0] = 2 * (x * y + z * w);     m[1][1] = 1 - 2 * (x * x + z * z); m[1][2] = 2 * (y * z - x * w);
    m[2][0] = 2 * (x * z - y * w);     m[2][1] = 2 * (y * z + x * w);     m[2][2] = 1 - 2 * (x * x + y * y);
    for (int i = 0; i < 3; ++i) { m[i][3] = 0; m[3][i] = pos[i]; }
    m[3][3] = 1;
}
static void roam_clone_offset(int clone, float pos[3]) { pos[0] += 5.0f * (float)clone; }
// EAX_CarState holds the render-space matrix (WorldBodyConn::Update 00768850 converts the body with bConvertFromBond);
// the sim (factory position/direction, IRigidBody setters) takes physics space: (x, y, z) -> (-y, z, x), as the
// position row of bConvertToBond 0045E1A0 maps it.
static void roam_to_bond(const float r[3], float b[3]) { b[0] = -r[1]; b[1] = r[2]; b[2] = r[0]; }

static uint32_t roam_spawn(Cpu *c, const float pos[3], const float m[4][4], const RoamRemote *remote, int slot) {
    uint32_t local = roam_local_vehicle();
    if (!local || !M32(local + 0x28)) return 0;
    uint32_t key = M32(M32(local + 0x28) + 0x20), custom = M32(local + 0x38);
    if (remote->has_look) {
        uint32_t lookup[2] = {0x4A97EC8Fu, remote->look.car}; // Attrib pvehicle class, explicit collection (no default)
        if (remote->look.car && roam_call(c, 0, 0x00455FD0u, 2, lookup)) {
            key = remote->look.car;
            custom = roam_mem + 0x41000 + (uint32_t)slot * 0x200;
            roam_call(c, custom, 0x0056F1B0u, 0, NULL); // FECustomizationRecord::Default
            if (remote->look.nparts == ROAM_LOOK_CUSTOM_WORDS) {
                for (int i = 0; i < 139; ++i) {
                    uint32_t part_index = (remote->look.parts[i/2] >> (16*(i%2))) & 0xffffu;
                    uint32_t part = part_index <= 32767 ? roam_call(c, 0x009B26A8u, 0x00747C00u, 1, &part_index) : 0;
                    M16(custom + 2*i) = part ? (uint16_t)part_index : 0xffffu;
                }
                // All fields here are scalar colour/skin values, never guest pointers.
                memcpy(G + custom + 0x138, remote->look.parts + 70, 22 * 4);
            } else custom = 0; // no record means the renderer keeps this model's stock parts

        }
    }
    uint32_t p = roam_mem + 0x40000 + 0x100, dir = p + 0x40, at = p + 0x50, hash = p + 0x60;
    memset(G + p, 0, 0x70);
    float bdir[3], bpos[3];
    roam_to_bond(m[0], bdir);                         // render row 0 (forward) is bConvertToBond's row 2
    roam_to_bond(pos, bpos);
    memcpy(G + dir, bdir, 12);
    memcpy(G + at, bpos, 12);
    roam_call(c, 0, 0x004040F0u, 1, &hash);          // VehicleParams::TypeName(out)
    M32(p + 0x00) = M32(hash);
    M32(p + 0x04) = 0xA6B47FACu;
    M32(p + 0x08) = p;
    M32(p + 0x10) = 6;                                // driver class: remote -> RBRemote (0067F0D0)
    M32(p + 0x14) = key;
    M32(p + 0x18) = dir;
    M32(p + 0x1c) = at;
    M32(p + 0x20) = custom;
    M32(p + 0x2c) = 8;                                // racers pass 9; bit 1 skips ECommitRenderAssets (0075D320), so the car is never drawn
    uint32_t name = 0x0089121Cu;                      // "PVehicle"
    uint32_t args[4] = {roam_call(c, 0, 0x005CC240u, 1, &name), M32(p), M32(p + 4), p};
    return roam_call(c, 0, 0x0041CB10u, 4, args);    // Factory<Sim::Param,ISimable,UCrc32>::CreateInstance
}
static void roam_kill(Cpu *c, RoamCar *k) {
    if (k->sim && roam_vehicle_listed(k->sim + 0x80)) {
        roam_call(c, k->sim, M32(M32(k->sim) + 8), 0, NULL);   // ISimable::Kill
        roam_killed++;
    }
    memset(k, 0, sizeof *k);
}
static void roam_write_pose(Cpu *c, uint32_t sim, const float m[4][4], const float vel[3]) {
    uint32_t rb = M32(sim + 0x4c);
    if (!rb) return;
    uint32_t vt = M32(rb), v = roam_mem + 0x40000 + 0x180, mat = v + 0x20, rmat = v + 0x60;
    float b[3];
    roam_to_bond(m[3], b);
    memcpy(G + v, b, 12);
    roam_call(c, rb, M32(vt + 0x5c), 1, &v);           // SetPosition 00696FB0
    roam_to_bond(vel, b);
    memcpy(G + v, b, 12);
    roam_call(c, rb, M32(vt + 0x60), 1, &v);           // SetLinearVelocity 00696FD0
    memset(G + v, 0, 12);
    roam_call(c, rb, M32(vt + 0x64), 1, &v);           // SetAngularVelocity 00696FF0
    memcpy(G + rmat, m, 64);
    uint32_t conv[2] = {mat, rmat};
    roam_call(c, 0, 0x0045E1A0u, 2, conv);             // bConvertToBond(out, in)
    roam_call(c, rb, M32(vt + 0x70), 1, &mat);         // SetOrientation(Matrix4) 00696D90
}
// Spawn, pose and remove the friends' cars; `now` is this frame's ms.
// The game keeps cars only where its world is streamed in: a friend's car ~2.1 km away was removed from the vehicle
// list and every spawn there failed. Spawn within ROAM_SPAWN_M of the local car, remove beyond ROAM_DROP_M.
#define ROAM_SPAWN_M 300.0f
#define ROAM_DROP_M 400.0f
#define ROAM_GHOST_CLEAR_M 6.0f   // centres this far apart cannot overlap (car ~4.7 x 1.9 m), so collisions resume
static float roam_dist_local(const float p[3]) {
    uint32_t car = (int32_t)M32(0x008EF998u) >= 1 ? M32(0x009142D0u) : 0, st = car ? M32(car + 0x34) : 0;
    if (!st) return 1e9f;
    float d[3] = {p[0] - MF32(st + 0x40), p[1] - MF32(st + 0x44), p[2] - MF32(st + 0x48)};
    return sqrtf(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
}
typedef struct {
    uint16_t id;
    uint8_t clone;
    float pos[3], q[4], vel[3], dist, score;
    int friend;
} RoamChoice;
static RoamCar *roam_car_find(uint16_t id, int clone) {
    for (int k = 0; k < ROAM_CARS; ++k)
        if (roam_car[k].used && roam_car[k].id == id && roam_car[k].clone == clone) return &roam_car[k];
    return NULL;
}
static int roam_choice_before(const RoamChoice *a, const RoamChoice *b) {
    if (a->friend != b->friend) return a->friend > b->friend;
    if (a->score != b->score) return a->score < b->score;
    if (a->id != b->id) return a->id < b->id;
    return a->clone < b->clone;
}
static void roam_cars(Cpu *c, uint32_t now, int has_local) {
    uint64_t begin = diag_on ? host_ticks() : 0;
    if (!roam_mem && has_local) roam_mem = galloc(0x42000);
    for (int k = 0; k < ROAM_CARS; ++k) { // world unload owns its own destruction
        RoamCar *car = &roam_car[k];
        if (car->used && !roam_vehicle_listed(car->sim + 0x80)) { memset(car, 0, sizeof *car); roam_lost++; }
    }
    RoamChoice chosen[ROAM_CAR_BUDGET];
    int n = 0;
    for (int i = 0; has_local && i < ROAM_MAX_REMOTE; ++i) {
        const RoamRemote *r = &roam.view.r[i];
        if (!r->used || !r->n || r->map_only || (uint32_t)(now - r->last_rx) > 1000 ||
            !(r->s[r->n - 1].flags & ROAM_FLAG_CAR)) continue;
        float pos[3], q[4], vel[3];
        if (!roam_view_pose(&roam.view, r->id, now, pos, q, vel)) continue;
        if (!roam_pose_valid(pos, vel)) continue;
        for (int clone = 0; clone < roam_clones; ++clone) {
            RoamChoice a = {.id=r->id, .clone=(uint8_t)clone};
            memcpy(a.pos, pos, sizeof pos); memcpy(a.q, q, sizeof q); memcpy(a.vel, vel, sizeof vel);
            roam_clone_offset(clone, a.pos);
            a.dist = roam_dist_local(a.pos);
            int incumbent = roam_car_find(r->id, clone) != NULL;
            if (a.dist > (incumbent ? ROAM_DROP_M : ROAM_SPAWN_M)) continue;
            for (int j = 0; j < roam.npins; ++j) if (roam.pins[j] == r->id) a.friend = 1;
            a.score = a.dist * (incumbent ? 0.9f : 1.0f);
            int at = 0;
            while (at < n && !roam_choice_before(&a, &chosen[at])) ++at;
            if (at >= ROAM_CAR_BUDGET) continue;
            if (n < ROAM_CAR_BUDGET) ++n;
            for (int j = n - 1; j > at; --j) chosen[j] = chosen[j - 1];
            chosen[at] = a;
        }
    }
    uint64_t selected = diag_on ? host_ticks() : 0;
    int changed = 0;
    // Retire before adding: even an arrival burst never exceeds six actual vehicles.
    for (int k = 0; k < ROAM_CARS; ++k) {
        RoamCar *car = &roam_car[k];
        if (!car->used) continue;
        int keep = 0;
        for (int j = 0; j < n; ++j) if (chosen[j].id == car->id && chosen[j].clone == car->clone) keep = 1;
        const RoamRemote *remote = roam_view_find(&roam.view, car->id);
        if (remote && remote->has_look && car->look_rev != remote->look.rev) keep = 0;
        if (keep) continue;
        car->ghost = 1;
        if (!changed && !roam_waiting(&roam_lifecycle_wait, now)) {
            roam_kill(c, car); changed = 1; roam_lifecycle_wait = (RoamWait){now, ROAM_LIFECYCLE_MS};
        }
    }
    for (int j = 0; j < n && !changed; ++j) {
        RoamChoice *a = &chosen[j];
        if (roam_car_find(a->id, a->clone) || roam_waiting(&roam_lifecycle_wait, now) ||
            roam_waiting(&roam_retry_wait, now)) continue;
        RoamCar *slot = NULL;
        for (int k = 0; k < ROAM_CARS; ++k) if (!roam_car[k].used) { slot = &roam_car[k]; break; }
        if (!slot) break;
        float m[4][4]; roam_quat_rows4(a->q, a->pos, m);
        const RoamRemote *remote = roam_view_find(&roam.view, a->id);
        uint32_t sim = roam_spawn(c, a->pos, m, remote, (int)(slot - roam_car));
        changed = 1; roam_lifecycle_wait = (RoamWait){now, ROAM_LIFECYCLE_MS};
        if (!sim) {
            roam_spawn_fail++; roam_retry_wait = (RoamWait){now, 1000};
            diag_event("roam: spawn of friend %u car %d failed (%u failures)", a->id, a->clone, roam_spawn_fail);
        } else {
            *slot = (RoamCar){.id=a->id, .clone=a->clone, .used=1, .sim=sim, .ghost=1, .look_rev=remote->has_look ? remote->look.rev : 0};
            roam_spawned++;
            diag_event("roam: spawned friend %u car %d sim %08X at %.1f %.1f %.1f", a->id, a->clone, sim, a->pos[0], a->pos[1], a->pos[2]);
        }
    }
    uint64_t lifecycle = diag_on ? host_ticks() : 0;
    for (int j = 0; j < n; ++j) {
        RoamChoice *a = &chosen[j]; RoamCar *car = roam_car_find(a->id, a->clone);
        if (!car) continue;
        const RoamRemote *r = roam_view_find(&roam.view, a->id);
        if (roam_ghost_mode || (r->s[r->n - 1].flags & ROAM_FLAG_GHOST)) car->ghost = 1;
        else if (car->ghost && a->dist > ROAM_GHOST_CLEAR_M) car->ghost = 0;
        float m[4][4]; roam_quat_rows4(a->q, a->pos, m);
        roam_write_pose(c, car->sim, m, a->vel);
    }
    roam_ghosts = 0;
    int active = 0;
    for (int k = 0; k < ROAM_CARS; ++k) { active += !!roam_car[k].used; roam_ghosts += roam_car[k].used && roam_car[k].ghost; }
    if (roam_log) fprintf(roam_log, "P %u active %d selected %d select_ms %.6f lifecycle_ms %.6f pose_ms %.6f\n", now,
        active, n, (selected-begin)/1e6, (lifecycle-selected)/1e6, (host_ticks()-lifecycle)/1e6);
}

int roam_game_hud(RoamHudPlayer *out, int capacity) {
    if (!atomic_load_explicit(&roam_online_active, memory_order_relaxed) || !roam.welcomed) return 0;
    uint32_t now = roam_ms(); int n = 0;
    for (int i = 0; i < roam.nmembers && n < capacity; ++i) {
        const RoamRemote *r = roam_view_find(&roam.view, roam.members[i]);
        if (!r || !r->n || (uint32_t)(now - r->last_rx) > 1000 || !(r->s[r->n-1].flags & ROAM_FLAG_CAR)) continue;
        RoamHudPlayer *p = &out[n++]; memset(p, 0, sizeof *p); p->id = r->id;
        snprintf(p->name, sizeof p->name, "%s", *r->name ? r->name : "Player");
        float q[4], vel[3];
        if (!roam_view_pose(&roam.view, r->id, now, p->pos, q, vel)) {
            memcpy(p->pos, r->s[r->n-1].pos, sizeof p->pos); q[0]=q[1]=q[2]=0; q[3]=1;
        }
        float m[4][4]; roam_quat_rows4(q, p->pos, m);
        p->direction[0] = m[0][0]; p->direction[1] = m[0][1];
        memcpy(p->label_pos, p->pos, sizeof p->pos);
        int clone = 256;
        for (int k = 0; k < ROAM_CARS; ++k)
            if (roam_car[k].used && roam_car[k].id == r->id && roam_car[k].clone < clone) clone = roam_car[k].clone;
        p->drawable = clone < 256;
        if (p->drawable) roam_clone_offset(clone, p->label_pos);
    }
    return n;
}

// ---- Ghost mode: the F10 row, D-pad Down and G ----
static void roam_set_ghost(int on, const char *how) {
    on = on != 0;
    if (on == roam_ghost_mode) return;
    roam_ghost_mode = on;
    overlay_notice(on ? "Ghost mode on" : "Ghost mode off");
    if (diag_on) diag_event("roam: ghost mode %s (%s) at frame %llu", on ? "on" : "off", how, (unsigned long long)perf_nframes);
}
static void ghost_apply(int v) { roam_set_ghost(v, "menu"); }
static int ghost_get(void) { return roam_ghost_mode; }
static Setting s_ghost = {.id = "roam.ghost", .category = SET_ONLINE, .order = 50, .label = "Ghost mode",
    .help = "In online free roam your car and friends' cars pass through each other. D-pad Down or G while driving also toggles it.",
    .type = SET_TOGGLE, .def = 0, .pref = "", .apply = ghost_apply, .get = ghost_get};
static void online_host(int v) { (void)v; roam_online_host(settings_text("online.public_ip")); }
static void online_join(int v) { (void)v; roam_online_join(settings_text("online.code")); }
static void online_leave(int v) { (void)v; roam_online_leave(); }
static Setting s_host = {.id="online.host", .category=SET_ONLINE, .order=10, .label="Host", .type=SET_ACTION,
    .help="Start a free-roam session and show a code for friends.", .pref="", .apply=online_host};
static Setting s_host_code = {.id="online.host_code", .category=SET_ONLINE, .order=15, .label="Host code (private)",
    .type=SET_INFO, .pref="", .value_text=roam_online_host_code,
    .help="Send privately to your friend. Contains your internet address; not a password."};
static Setting s_code = {.id="online.code", .category=SET_ONLINE, .order=20, .label="Join code", .type=SET_TEXT,
    .help="Type or paste your friend's XXXX-XXXXX code, then press Enter to join.", .pref="", .apply=online_join};
static Setting s_join = {.id="online.join", .category=SET_ONLINE, .order=30, .label="Join", .type=SET_ACTION,
    .help="Connect using the code above.", .pref="", .apply=online_join};
static Setting s_leave = {.id="online.leave", .category=SET_ONLINE, .order=40, .label="Leave", .type=SET_ACTION,
    .help="Disconnect and close the hosted session and router port.", .pref="", .apply=online_leave};
static Setting s_status = {.id="online.status", .category=SET_ONLINE, .order=60, .label="Status", .type=SET_INFO,
    .pref="", .value_text=roam_online_status};
static Setting s_public_ip = {.id="online.public_ip", .category=SET_ONLINE, .order=70, .label="Public IPv4 (optional)",
    .help="Only needed if your router cannot report its address. Forward UDP 47800, enter your public IPv4, then Host.",
    .type=SET_TEXT, .pref=""};
__attribute__((constructor)) static void roam_register(void) {
    settings_add(&s_host); settings_add(&s_host_code); settings_add(&s_code); settings_add(&s_join); settings_add(&s_leave);
    settings_add(&s_ghost); settings_add(&s_status); settings_add(&s_public_ip);
}

// Driving, as user32.m's pad layout decides it: racing (game-flow 6), the sim not paused, no menu package in control
// (cFEng::FindPackageWithControl 00516A90; the HUD packages are HUD_*).
static int roam_driving(const char **pkg) {
    uint32_t sim = M32(0x009885E0u), fe = M32(0x0091CADCu) ? M32(M32(0x0091CADCu)) : 0;
    const char *ctl = NULL;
    if (fe && fe != 0xFFFFFF28u)
        for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) if (M32(p + 0x20)) { ctl = gptr(M32(p + 0xC)); break; }
    *pkg = ctl ? ctl : "none";
    return M32(0x00925E90u) == 6 && !(sim && M32(sim + 0x2C) == 4) && !(ctl && strncasecmp(ctl, "HUD_", 4));
}
static void roam_toggle_poll(int has_car) {
    int in = user32_ghost_buttons();
    const char *pkg;
    int driving = roam_driving(&pkg);
    int ok = roam_toggle_allowed(roam.welcomed, has_car, driving, portmenu_is_open() || osk_visible());
    RoamToggle *t[2] = {&roam_toggle_pad, &roam_toggle_key};
    static const char *const how[2] = {"pad", "key"};
    for (int k = 0; k < 2; ++k) {
        int down = (in >> k) & 1, was = t[k]->held;
        if (roam_toggle_press(t[k], down, ok)) roam_set_ghost(!roam_ghost_mode, how[k]);
        else if (down && !was && diag_on)
            diag_event("roam: ghost toggle refused (%s) at frame %llu: driving %d, package %s, menu %d, car %d", how[k],
                       (unsigned long long)perf_nframes, driving, pkg, portmenu_is_open() || osk_visible(), has_car);
    }
}

static void roam_send_look(uint32_t now, uint8_t *packet) {
    if ((uint32_t)(now - roam_last_look) < 1000) return;
    uint32_t local = roam_local_vehicle();
    if (!local || !M32(local + 0x28)) return;
    RoamLook look = {.car=M32(M32(local + 0x28) + 0x20)};
    uint32_t custom = M32(local + 0x38);
    if (custom) {
        look.nparts = ROAM_LOOK_CUSTOM_WORDS;
        for (int i = 0; i < 139; ++i) look.parts[i/2] |= (uint32_t)M16(custom + 2*i) << (16*(i%2));
        memcpy(look.parts + 70, G + custom + 0x138, 22 * 4);
    }
    look.rev = roam_local_look.rev;
    if (memcmp(&look, &roam_local_look, sizeof look)) {
        look.rev++; if (!look.rev) look.rev = 1;
        roam_local_look = look;
    }
    roam_last_look = now;
    roam_send(packet, roam_client_look(&roam, &roam_local_look, packet));
}
static void roam_frame(Cpu *c) {
    uint32_t now = roam_ms();
    uint64_t phase = diag_on ? host_ticks() : 0;
    uint8_t b[ROAM_MAX_DGRAM];
    for (int i = 0; i < 64; ++i) {
        ssize_t n = recv(roam_fd, b, sizeof b, 0);
        if (n <= 0) break;
        roam_rx++;
        int was = roam.welcomed; uint32_t bad = roam.rx_bad;
        roam_client_input(&roam, b, (int)n, now);
        if (roam.rx_bad == bad) roam_last_rx = now;
        if (!was && roam.welcomed) diag_event("roam: welcomed, id %u near %d", roam.id, roam.near);
    }
    roam_view_expire(&roam.view, now, 1000);
    if (roam.welcomed && (uint32_t)(now - roam_last_rx) > 5000) roam.welcomed = 0;
    // A periodic HELLO is also a heartbeat when alone.
    if (now - roam_last_hello >= (roam.welcomed ? 2000u : 500u)) {
        roam_last_hello = now; roam_send(b, roam_client_hello(&roam, b));
    }
    if (roam.welcomed) {
        roam_send_look(now, b);
        RoamState s;
        int has = roam_local(&s, now);
        if (has && roam_log) fprintf(roam_log, "L %u %.3f %.3f %.3f\n", now, s.pos[0], s.pos[1], s.pos[2]);
        if (now - roam_last_state >= 1000 / ROAM_HZ) {
            roam_last_state = now;
            if (!has) { memset(&s, 0, sizeof s); s.t = now; s.q[3] = 32767; }
            roam_send(b, roam_client_state(&roam, &s, b));
        }
    }
    if (roam_log) fprintf(roam_log, "N %u network_ms %.6f\n", now, (host_ticks()-phase)/1e6);
    int remotes = 0;
    for (int i = 0; i < ROAM_MAX_REMOTE; ++i) {
        const RoamRemote *r = &roam.view.r[i];
        if (!r->used || !r->n) continue;
        float pos[3], q[4], vel[3];
        if (!roam_view_pose(&roam.view, r->id, now, pos, q, vel)) continue;
        remotes++;
        static uint16_t seen_id[ROAM_MAX_REMOTE];
        static uint8_t seen_ghost[ROAM_MAX_REMOTE];
        int rg = (r->s[r->n - 1].flags & ROAM_FLAG_GHOST) != 0;
        if (diag_on && (seen_id[i] != r->id || seen_ghost[i] != rg)) {
            if (seen_id[i] != r->id ? rg : 1)
                diag_event("roam: remote %u ghost %s at frame %llu", r->id, rg ? "on" : "off", (unsigned long long)perf_nframes);
            seen_id[i] = r->id; seen_ghost[i] = (uint8_t)rg;
        }
        if (roam_log && (r->s[r->n - 1].flags & ROAM_FLAG_CAR))
            fprintf(roam_log, "R %u %u %.3f %.3f %.3f %.4f %.4f %.4f %.4f\n", r->id, now, pos[0], pos[1], pos[2],
                    q[0], q[1], q[2], q[3]);
    }
    int has_car = roam_local_vehicle() != 0;
    roam_cars(c, now, has_car);
    if (roam.welcomed) roam_toggle_poll(has_car);
    roam_online_players(roam.welcomed, remotes + 1, roam.last_err ? roam.last_err : !roam.welcomed && now - roam_started > 15000 ? 4 : 0);
    if (now - roam_last_sec >= 1000) {
        roam_last_sec = now;
        if (roam_log) fflush(roam_log);
        if (diag_on)
            diag_event("roam: id %u welcomed %d remotes %d tx %u rx %u bundles %u entries %u bad %u err %d cars spawned %u failed %u killed %u lost %u ghosts %d skips %u",
                       roam.id, roam.welcomed, remotes, roam_tx, roam_rx, roam.rx_bundles, roam.rx_entries, roam.rx_bad,
                       roam.last_err, roam_spawned, roam_spawn_fail, roam_killed, roam_lost, roam_ghosts, roam_ghost_skips);
    }
}

static void roam_session(Cpu *c) {
    int state = atomic_load(&roam_state);
    if (state < 0) return;
    if (state != 1) {
        // Consume the notification before reading the locked target, preserving a concurrent new request.
        atomic_exchange(&roam_state, 1);
        char host[64]; int port; uint32_t room;
        int action = roam_online_target(host, &port, &room);
        if (action) {
            if (roam_fd >= 0) { uint8_t b[ROAM_HDR]; roam_send(b, roam_client_bye(&roam, b)); close(roam_fd); roam_fd = -1; }
            for (int k = 0; k < ROAM_CARS; ++k) if (roam_car[k].used) roam_kill(c, &roam_car[k]);
            roam_ghosts = 0; memset(&roam, 0, sizeof roam);
            memset(&roam_local_look, 0, sizeof roam_local_look); roam_last_look = 0;
            roam_toggle_pad.held = roam_toggle_key.held = 0;
            roam_last_hello = roam_last_state = roam_last_sec = roam_tx = roam_rx = 0;
            memset(&roam_retry_wait, 0, sizeof roam_retry_wait);
            memset(&roam_lifecycle_wait, 0, sizeof roam_lifecycle_wait);
            if (roam_log) { fclose(roam_log); roam_log = NULL; }
            if (action > 0) { char endpoint[96]; snprintf(endpoint, sizeof endpoint, "%s:%d", host, port); roam_fd = roam_open(endpoint, room); }
            roam_env_checked = 1;
            roam_online_players(0, 0, action > 0 && roam_fd < 0 ? 3 : 0);
        } else if (!roam_env_checked) { roam_env_checked = 1; roam_fd = roam_open(NULL, 0); }
        atomic_store_explicit(&roam_online_active, roam_fd >= 0, memory_order_relaxed);
        if (roam_fd < 0) { int expected = 1; atomic_compare_exchange_strong(&roam_state, &expected, -1); return; }
    }
}

// MainLoop runs in the front end too, where World_Service is skipped without a simulation.
// Consume session requests on the game thread before it services or displays the next frame.
void f_00663D30_orig(Cpu *c);
void f_00663D30(Cpu *c) {
    roam_session(c);
    f_00663D30_orig(c);
}

void f_0075AAD0_orig(Cpu *c);
void f_0075AAD0(Cpu *c) {   // World_Service (cdecl, no arguments)
    f_0075AAD0_orig(c);
    roam_session(c);
    if (atomic_load(&roam_state) == 1 && roam_fd >= 0) roam_frame(c);
}

// RBVehicle::CanCollideWith(const RigidBody &) (0069B740, thiscall, one argument; every vehicle body's vtable slot +0x48,
// asked both ways by RigidBody::OnObjectOverlap 006998C0, so one false skips the pair): a ghosted friend car collides
// with nothing. The body is the IRigidBody interface [ISimable+0x4c] minus 0x48 (RBRemote constructor 006B8650).
void f_0069B740_orig(Cpu *c);
void f_0069B740(Cpu *c) {
    if (roam_ghosts) {
        uint32_t other = arg(c, 0);
        for (int k = 0; k < ROAM_CARS; ++k) {
            const RoamCar *car = &roam_car[k];
            if (!car->used || !car->ghost) continue;
            uint32_t rb = M32(car->sim + 0x4c), body = rb ? rb - 0x48 : 0;
            if (body && (body == c->ecx || body == other)) {
                roam_ghost_skips++;
                c->eax &= ~0xFFu;            // bool false
                c->esp += 8;                 // __thiscall, one stack argument
                return;
            }
        }
    }
    f_0069B740_orig(c);
}
