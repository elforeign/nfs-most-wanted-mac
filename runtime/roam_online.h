#pragma once
#include <stdint.h>
#include <stdatomic.h>
/* One relaxed load in presentation; no session polling or router work when off. */
extern atomic_int roam_online_active;
static inline int roam_online_limit(int limit, int online) {
    return online && (limit <= 0 || limit > 60) ? 60 : limit;
}
#define ROAM_CAR_BUDGET 6
#define ROAM_LIFECYCLE_MS 200
typedef struct {
    uint16_t id;
    char name[16];
    float pos[3], label_pos[3], direction[2];
    int drawable;
} RoamHudPlayer;
int roam_game_hud(RoamHudPlayer *out, int capacity);
/* Diagnostic router fixtures only; the beta endpoint lives in roam_shared_endpoint.h. */
#define ROAM_CODE_PORT 47800
#define ROAM_CODE_SIZE 13
#define ROAM_CODE_VERSION 1
/* Room format version and nonzero room only; never an endpoint. */
int roam_code_encode(uint32_t room, char out[ROAM_CODE_SIZE]);
int roam_code_decode(const char *code, uint32_t *room);
/* Session-local errors 3/4 are connection/unavailable; 5 matches ROAM_ERR_BANNED on the wire. */
#define ROAM_ONLINE_BANNED 5
/* Session requests are consumed on the game thread. 1 target, -1 leave, 0 unchanged. */
void roam_online_create(void);
void roam_online_join(const char *code);
void roam_online_leave(void);
int roam_online_target(char host[64], int *port, uint32_t *room);
void roam_online_status(char *out, int n);
void roam_online_host_code(char *out, int n);
void roam_online_players(int welcomed, int players, int error);
                                                                                           
typedef struct RoamServer RoamServer;
RoamServer *roam_server_start(uint32_t bind_ip, uint16_t port);
uint16_t roam_server_port(RoamServer *s);
void roam_server_stop(RoamServer *s);
/* A successful hairpin probe confirms only this router path, not outside-network reachability. */
int roam_server_probe(uint32_t ip, uint16_t port, const atomic_int *stop);
