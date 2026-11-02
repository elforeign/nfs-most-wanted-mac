// Beta sessions use a shared service; this controller never opens a socket or router mapping.
#include "roam_online.h"
#include "roam_shared_endpoint.h"
#include <arpa/inet.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void roam_game_wake(void);
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int mode, target_kind, welcomed, ever_welcomed, players, session_error;
static char action_error[160], code[ROAM_CODE_SIZE], target_host[64];
static int target_port;
static uint32_t target_room;
static int endpoint(char host[64], int *port) {
    struct in_addr a;
    const char *ip = ROAM_SHARED_IPV4;
    int p = ROAM_SHARED_PORT;
    if (!ip || inet_pton(AF_INET, ip, &a) != 1 || p <= 0 || p > 65535) return 0;
    uint32_t v = ntohl(a.s_addr);
    if (!v || (v >> 24) == 0 || (v >> 24) >= 224) return 0;
    snprintf(host, 64, "%s", ip); *port = p; return 1;
}
static void reset(void) {
    mode = welcomed = ever_welcomed = players = session_error = 0;
    *code = *action_error = 0;
}
void roam_online_leave(void) {
    pthread_mutex_lock(&lock); reset(); target_kind = -1;
    pthread_mutex_unlock(&lock); roam_game_wake();
}
static void begin(int create, uint32_t room) {
    char host[64] = ""; int port = 0;
    pthread_mutex_lock(&lock);
    if (!endpoint(host, &port)) {
        // Consume any old request so no diagnostic fallback can start networking.
        reset(); target_kind = -1;
        snprintf(action_error, sizeof action_error,
            "Online unavailable: no shared relay configured. Online free roam arrives in a later update.");
    } else {
        reset(); mode = create ? 1 : 2;
        snprintf(target_host, sizeof target_host, "%s", host);
        target_port = port; target_room = room; target_kind = 1;
        if (create) roam_code_encode(room, code);
    }
    pthread_mutex_unlock(&lock); roam_game_wake();
}
void roam_online_create(void) {
    uint32_t room;
    do { arc4random_buf(&room, sizeof room); } while (!room);
    begin(1, room);
}
void roam_online_join(const char *text) {
    uint32_t room;
    if (roam_code_decode(text, &room)) {
        pthread_mutex_lock(&lock);
        snprintf(action_error, sizeof action_error, "Invalid code. Use R-XXXX-XXXXX from Create room; old endpoint codes are unsupported.");
        pthread_mutex_unlock(&lock); return;
    }
    begin(0, room);
}
int roam_online_target(char host[64], int *port, uint32_t *room) {
    pthread_mutex_lock(&lock); int kind = target_kind; target_kind = 0;
    if (kind == 1) { snprintf(host, 64, "%s", target_host); *port = target_port; *room = target_room; }
    pthread_mutex_unlock(&lock); return kind;
}
void roam_online_players(int w, int p, int error) {
    pthread_mutex_lock(&lock);
    welcomed = w; if (w) ever_welcomed = 1;
    players = p; session_error = error;
    pthread_mutex_unlock(&lock);
}
void roam_online_status(char *out, int n) {
    pthread_mutex_lock(&lock);
    const char *message = NULL;
    if (*action_error) message = action_error;
    else if (!mode) message = "Off";
    else if (session_error == 1) message = "Relay uses a different game version. Install the matching release.";
    else if (session_error == 2) message = "Relay is full. Try again later.";
    else if (session_error == ROAM_ONLINE_BANNED) message = "This installation is banned from the relay.";
    else if (session_error == 3) message = "Online unavailable: cannot open the relay connection or installation credential. Check your profile and try again.";
    else if (ever_welcomed && !welcomed) message = "Reconnecting to the shared relay... If it stays unavailable, try again later.";
    else if (session_error) message = "Relay unavailable: no reply. Check your connection and try again later.";
    else if (!welcomed) message = mode == 1 ? "Creating room..." : "Joining room...";
    if (message) snprintf(out, n, "%s", message);
    else snprintf(out, n, "Connected: %d players", players);
    pthread_mutex_unlock(&lock);
}
void roam_online_host_code(char *out, int n) {
    pthread_mutex_lock(&lock); snprintf(out, n, "%s", mode == 1 ? code : ""); pthread_mutex_unlock(&lock);
}
