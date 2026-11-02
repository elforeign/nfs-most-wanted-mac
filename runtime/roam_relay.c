// UDP relay: hashed endpoints, 5 Hz spatial selection, 32 bounded interests, tiered snapshots.
#include "roam_core.h"
#include "roam_admission.h"
#include "roam_moderation.h"
#include <stdio.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>

#define TIMEOUT_MS 5000
#define PKT_RATE 40.0f
#define PKT_BURST 20.0f
#define BYTE_RATE 8192.0f
#define BYTE_BURST 4096.0f
#define LOOK_RESEND_MS 2000
#define HBUCKETS 4096
#define LEAF_MAX 16
// Mutated by the load driver; these are compile-time proof controls, never user settings.
#ifndef ROAM_NO_HYSTERESIS
#define ROAM_NO_HYSTERESIS 0
#endif
#ifndef ROAM_NO_STAGGER
#define ROAM_NO_STAGGER 0
#endif
#ifndef ROAM_LINEAR_LOOKUP
#define ROAM_LINEAR_LOOKUP 0
#endif

typedef struct {
    uint16_t id, look_rev;
    uint8_t tier;
    uint32_t look_ms;
    uint32_t name_ms;
} Interest;
typedef struct {
    int used, anext, free_next;
    struct sockaddr_in addr;
    uint16_t id, seq, txseq, member_seq;
    uint32_t room, last_rx, state_ms, tok_ms;
    char name[16];
    uint8_t identity[32], proof[32];
    int has_state, has_look;
    RoamState st;
    RoamLook look;
    float tok_pkt, tok_byte;
    Interest nb[ROAM_INTEREST_MAX];
    int nnb, look_cursor, service_turn, members_pending;
    uint16_t pins[ROAM_PIN_MAX];
    int npins;
} Client;
// A coarse population is subdivided to <=16 occupants along its longest spatial axis.
// Bounds include the next half second of travel, so approaching cars cannot be pruned.
// Room ranges split before spatial cells; co-located leaves prune using their lowest id.
typedef struct {
    float lo[3], hi[3];
    uint32_t room_lo, room_hi;
    uint16_t min_id;
    int left, right, start, count;
} Cell;
typedef struct {
    uint32_t ip, last;
    int used;
    RoamBucket admission;
} Source;
#define PROOF_SLOTS 2048
typedef struct { uint8_t digest[32]; uint64_t expiry; } UsedProof;
struct RoamRelay {
    int fd, near, max, free_head;
    Client *c;
    int ahead[HBUCKETS], *ids, *order;
    Cell *cells;
    int ncells, root;
    uint32_t last_tick, last_select, tick;
    int ticked, selected;
    RoamRelayPolicy policy;
    uint8_t secret[32], previous_secret[32];
    uint64_t absolute_ms, key_ms, key_clock_ms;
    int key_started, previous_key;
    Source sources[ROAM_SOURCES];
    RoamBucket replies[ROAM_SOURCES];
    RoamBucket admissions, challenges, ingress_packets, ingress_bytes, egress_packets, egress_bytes;
    uint32_t now;
    int time_started;
    unsigned output_cursor;
    RoamRelaySend send;
    void *send_context;
    RoamRelayStats st;
    uint16_t next_id;
    RoamModeration *moderation;
    RoamBucket signature_budget;
    UsedProof proofs[PROOF_SLOTS];
};
static uint64_t clock_us(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000 + (uint64_t)t.tv_nsec / 1000;
}
// Service may finish slightly after a caller's sampled clock. Never mint credit from a stale sample.
static uint32_t relay_time(RoamRelay *r, uint32_t now) {
    if (!r->time_started) { r->absolute_ms = now; r->now = now; }
    else if ((int32_t)(now - r->now) >= 0) {
        r->absolute_ms += (uint32_t)(now - r->now); r->now = now;
    }
    r->time_started = 1; return r->now;
}
static int cookie_keys(RoamRelay *r) {
    uint64_t sampled = clock_us() / 1000;
    if (!r->key_started) {
        r->key_ms = r->absolute_ms; r->key_clock_ms = sampled; r->key_started = 1;
    }
    uint64_t logical_elapsed = r->absolute_ms - r->key_ms;
    // The input clock can miss a complete wrap while the process is paused.
    // Real monotonic time independently retires keys even if that sample looks recent.
    uint64_t elapsed = sampled - r->key_clock_ms;
    if (logical_elapsed > elapsed) elapsed = logical_elapsed;
    if (elapsed < ROAM_KEY_MS) return 1;
    uint8_t next[32];
    // Entropy failure refuses cookie work rather than extending an old key's lifetime.
    if (!roam_entropy(next)) return 0;
    r->previous_key = elapsed < 2 * ROAM_KEY_MS;
    if (r->previous_key) memcpy(r->previous_secret, r->secret, sizeof r->secret);
    else memset(r->previous_secret, 0, sizeof r->previous_secret);
    memcpy(r->secret, next, sizeof r->secret);
    r->key_ms += (logical_elapsed / ROAM_KEY_MS) * ROAM_KEY_MS;
    r->key_clock_ms = sampled;
    return 1;
}
static int same(const struct sockaddr_in *a, const struct sockaddr_in *b) {
    return a->sin_addr.s_addr == b->sin_addr.s_addr && a->sin_port == b->sin_port;
}
static unsigned ahash(const struct sockaddr_in *a) {
    uint32_t h = a->sin_addr.s_addr ^ ((uint32_t)a->sin_port * 0x9e3779b9u);
    h ^= h >> 16; h *= 0x85ebca6bu; h ^= h >> 13;
    return h & (HBUCKETS - 1);
}
RoamRelayPolicy roam_relay_policy(void) {
    return (RoamRelayPolicy){20,100,10,100,100,100,100,100,8000,512,2*1024*1024,256*1024,
                            8192,512,4*1024*1024,256*1024};
}
RoamRelay *roam_relay_new(int fd, int near, int max_clients) {
    return roam_relay_new_policy(fd, near, max_clients, NULL);
}
void roam_relay_sender(RoamRelay *r, RoamRelaySend send, void *context) {
    r->send = send; r->send_context = context;
}
RoamRelay *roam_relay_new_policy(int fd, int near, int max_clients, const RoamRelayPolicy *policy) {
    RoamRelay *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    if (!roam_entropy(r->secret)) { free(r); return NULL; }
    r->policy = policy ? *policy : roam_relay_policy();
    r->fd = fd; r->near = near < 1 ? 1 : near > ROAM_INTEREST_MAX ? ROAM_INTEREST_MAX : near;
    r->max = max_clients < 1 ? 1 : max_clients > 65000 ? 65000 : max_clients;
    r->c = calloc((size_t)r->max, sizeof *r->c);
    r->ids = calloc(65536, sizeof *r->ids);
    r->order = malloc((size_t)r->max * sizeof *r->order);
    r->cells = calloc((size_t)r->max * 2, sizeof *r->cells);
    if (!r->c || !r->ids || !r->order || !r->cells) { roam_relay_free(r); return NULL; }
    for (int i = 0; i < HBUCKETS; ++i) r->ahead[i] = -1;
    for (int i = 0; i < r->max; ++i) r->c[i].free_next = i + 1 < r->max ? i + 1 : -1;
    r->next_id = 1;
    if (fd >= 0) {
        int flags = fcntl(fd, F_GETFL, 0);
        if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK)) { roam_relay_free(r); return NULL; }
        // A full 20 Hz ingress burst can arrive while the previous tick is sending its bundles.
        // Use the same bounded receive queue in embedded, standalone and load runs.
        int buffer = 4 * 1024 * 1024;
        if (setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof buffer)) {
            buffer = 256 * 1024;
            (void)setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof buffer);
        }
    }
    return r;
}
void roam_relay_free(RoamRelay *r) {
    if (r) { free(r->c); free(r->ids); free(r->order); free(r->cells); free(r); }
}
RoamRelayStats roam_relay_stats(const RoamRelay *r) { return r->st; }
static int tx(RoamRelay *r, const struct sockaddr_in *to, const uint8_t *b, int n) {
    r->st.tx_attempts++;
    roam_refill(&r->egress_packets, r->now, r->policy.egress_packets, r->policy.egress_packet_burst);
    roam_refill(&r->egress_bytes, r->now, r->policy.egress_bytes, r->policy.egress_byte_burst);
    if (r->egress_packets.tokens < 1000 || r->egress_bytes.tokens < (uint64_t)(n + 28) * 1000) {
        r->st.tx_suppressed++; r->st.tx_suppressed_bytes += (uint64_t)n + 28; return 0;
    }
    (void)roam_take(&r->egress_packets, 1); (void)roam_take(&r->egress_bytes, (uint32_t)n + 28);
    ssize_t sent = r->send ? r->send(r->send_context, to, b, n) : r->fd < 0 ? n : sendto(r->fd, b, (size_t)n, MSG_DONTWAIT, (const struct sockaddr *)to, sizeof *to);
    if (sent == n) { r->st.tx++; r->st.tx_bytes += (uint64_t)n; r->st.tx_wire_bytes += (uint64_t)n + 28; return 1; }
    r->st.tx_errors++; r->st.tx_error_bytes += (uint64_t)n;
    if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) r->st.tx_wouldblock++;
    return 0;
}
static Client *by_addr(RoamRelay *r, const struct sockaddr_in *a) {
    if (ROAM_LINEAR_LOOKUP) {
        for (int i = 0; i < r->max; ++i) {
            r->st.lookup_probes++;
            if (r->c[i].used && same(&r->c[i].addr, a)) return &r->c[i];
        }
    } else {
        for (int i = r->ahead[ahash(a)]; i >= 0; i = r->c[i].anext) {
            r->st.lookup_probes++;
            if (same(&r->c[i].addr, a)) return &r->c[i];
        }
    }
    return NULL;
}
static Client *by_id(RoamRelay *r, uint16_t id) { return r->ids[id] ? &r->c[r->ids[id] - 1] : NULL; }
static void drop_client(RoamRelay *r, Client *k) {
    int slot = (int)(k - r->c), *p = &r->ahead[ahash(&k->addr)];
    while (*p >= 0 && *p != slot) p = &r->c[*p].anext;
    if (*p == slot) *p = k->anext;
    r->ids[k->id] = 0;
    memset(k, 0, sizeof *k); k->free_next = r->free_head; r->free_head = slot;
    r->st.clients--;
}
static void expire_sources(RoamRelay *r, uint32_t now) {
    for (int i = 0; i < ROAM_SOURCES; ++i) {
        // Fixed reply buckets survive source expiry; keep their samples inside the clock horizon.
        RoamBucket *reply = &r->replies[i];
        if (reply->started) roam_refill(reply, now, r->policy.source_rate, r->policy.source_burst);
        Source *s = &r->sources[i];
        if (s->used && (uint32_t)(now - s->last) >= ROAM_SOURCE_MS) {
            memset(s, 0, sizeof *s); r->st.source_records--;
        }
    }
}
// Source identity is IPv4 only: changing ports cannot reset an address's allowance.
static Source *source(RoamRelay *r, const struct sockaddr_in *a, uint32_t now) {
    Source *found = NULL, *empty = NULL;
    for (int i = 0; i < ROAM_SOURCES; ++i) {
        Source *s = &r->sources[i];
        if (s->used && (uint32_t)(now - s->last) >= ROAM_SOURCE_MS) {
            memset(s, 0, sizeof *s); r->st.source_records--;
        }
        if (s->used && s->ip == a->sin_addr.s_addr) found = s;
        if (!s->used && !empty) empty = s;
    }
    if (!found) {
        if (!empty) { r->st.source_rejected++; return NULL; }
        found = empty; found->used = 1; found->ip = a->sin_addr.s_addr; r->st.source_records++;
        found->last = now;
    }
    return found;
}
static int reply_allowed(RoamRelay *r, const struct sockaddr_in *from, uint32_t now) {
    // Fixed, collision-sharing IPv4 buckets need no pre-validation records or eviction.
    uint32_t ip = ntohl(from->sin_addr.s_addr);
    ip ^= ip >> 16; ip *= 0x85ebca6bu; ip ^= ip >> 13;
    RoamBucket *replies = &r->replies[ip % ROAM_SOURCES];
    roam_refill(&r->challenges, now, r->policy.challenge_rate, r->policy.challenge_burst);
    roam_refill(replies, now, r->policy.source_rate, r->policy.source_burst);
    if (r->challenges.tokens < 1000 || replies->tokens < 1000) {
        r->st.challenge_suppressed++; return 0;
    }
    (void)roam_take(&r->challenges, 1); (void)roam_take(replies, 1); return 1;
}
// Only signature-verified HELLOs and previously admitted endpoints receive ban errors.
// Share challenge/full/version reply limits and the global packet/byte egress budgets.
static void banned_reply(RoamRelay *r, const struct sockaddr_in *to, uint32_t now) {
    if (!reply_allowed(r, to, now)) return;
    uint8_t e[ROAM_HDR + 1]; roam_hdr_put(e, ROAM_ERR, 0, 0); e[ROAM_HDR] = ROAM_ERR_BANNED;
    tx(r, to, e, sizeof e);
}
void roam_relay_input(RoamRelay *r, const uint8_t *b, int n, const struct sockaddr_in *from, uint32_t now) {
    int type; uint16_t id, seq;
    now = relay_time(r, now);
    r->st.rx++; if (n > 0) r->st.rx_bytes += (uint64_t)n;
    int h = roam_hdr_get(b, n, &type, &id, &seq);
    Client *k = h == 0 && n <= ROAM_MAX_DGRAM ? by_addr(r, from) : NULL;
    int admitted = k && id == k->id;
    // Unknown traffic can exhaust admission credit, never an admitted client's credit.
    if (!admitted) {
        roam_refill(&r->ingress_packets, now, r->policy.ingress_packets, r->policy.ingress_packet_burst);
        roam_refill(&r->ingress_bytes, now, r->policy.ingress_bytes, r->policy.ingress_byte_burst);
        uint32_t wire = n > 0 ? (uint32_t)n + 28 : 28;
        if (r->ingress_packets.tokens < 1000 || r->ingress_bytes.tokens < (uint64_t)wire * 1000) {
            r->st.ingress_dropped++; return;
        }
        (void)roam_take(&r->ingress_packets, 1); (void)roam_take(&r->ingress_bytes, wire);
    }
    if (h == -1 || n > ROAM_MAX_DGRAM) { r->st.rx_bad++; return; }
    if (k && !admitted && type != ROAM_HELLO) { r->st.rx_bad++; return; }
    if (admitted) {
        float dt = (float)(uint32_t)(now - k->tok_ms) / 1000.0f;
        k->tok_ms = now;
        k->tok_pkt = fminf(PKT_BURST, k->tok_pkt + dt * PKT_RATE);
        k->tok_byte = fminf(BYTE_BURST, k->tok_byte + dt * BYTE_RATE);
        if (k->tok_pkt < 1 || k->tok_byte < (float)n) { r->st.rx_rate_dropped++; return; }
        k->tok_pkt -= 1; k->tok_byte -= (float)n;
    }
    if (h == -2) {
        r->st.rx_version++;
        // Short requests cannot fund even an error. Unknown/rotating endpoints share reply limits.
        if (n < ROAM_HDR + 1 || !reply_allowed(r, from, now)) return;
        uint8_t e[ROAM_HDR + 1]; roam_hdr_put(e, ROAM_ERR, 0, 0); e[ROAM_HDR] = ROAM_ERR_VERSION;
        tx(r, from, e, sizeof e); return;
    }
    const uint8_t *p = b + ROAM_HDR; int len = n - ROAM_HDR;
    switch (type) {
    case ROAM_HELLO: {
        if (len != ROAM_HELLO_BASE && len != ROAM_HELLO_PROOF) { r->st.rx_bad++; return; }
        if (!cookie_keys(r)) { r->st.cookie_rejected++; return; }
        int validated = len == ROAM_HELLO_PROOF &&
            (roam_cookie_valid(r->secret, from, b[2], p, p + ROAM_HELLO_BASE, r->absolute_ms) ||
             (r->previous_key && roam_cookie_valid(r->previous_secret, from, b[2], p, p + ROAM_HELLO_BASE, r->absolute_ms)));
        if (!validated) {
challenge:
            r->st.cookie_rejected++;
            if (reply_allowed(r, from, now)) {
                uint8_t challenge[ROAM_HDR + 20]; roam_hdr_put(challenge, ROAM_CHALLENGE, 0, seq);
                if (roam_cookie_make(r->secret, from, b[2], p, r->absolute_ms, challenge + ROAM_HDR) &&
                    tx(r, from, challenge, sizeof challenge)) r->st.challenges++;
            }
            return;
        }
        uint8_t identity[32], proof[32];
        if (!roam_auth_identifier(p+20, identity)) { r->st.auth_rejected++; return; }
        // A live endpoint cannot replace its verified installation identity in place.
        if (k && memcmp(k->identity, identity, 32)) { r->st.auth_rejected++; return; }
        roam_auth_digest(p, ROAM_HELLO_BASE+20, proof);
        int used_slot = -1;
        if (!k || memcmp(k->proof, proof, 32)) {
            for (int i = 0; i < PROOF_SLOTS; ++i) {
                if (r->proofs[i].expiry <= r->absolute_ms) { if (used_slot < 0) used_slot = i; }
                else if (!memcmp(r->proofs[i].digest, proof, 32)) { r->st.replay_rejected++; goto challenge; }
            }
            if (used_slot < 0) { r->st.auth_limited++; return; }
        }
        uint32_t room = (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
        if (!k) {
            Source *src = source(r, from, now);
            if (!src) return;
            roam_refill(&r->admissions, now, r->policy.admission_rate, r->policy.admission_burst);
            roam_refill(&src->admission, now, r->policy.source_rate, r->policy.source_burst);
            if (r->admissions.tokens < 1000 || src->admission.tokens < 1000) {
                r->st.admission_rejected++; return;
            }
            (void)roam_take(&r->admissions, 1); (void)roam_take(&src->admission, 1);
            if (r->free_head < 0) {
                r->st.full++; uint8_t e[ROAM_HDR + 1];
                roam_hdr_put(e, ROAM_ERR, 0, 0); e[ROAM_HDR] = ROAM_ERR_FULL;
                if (reply_allowed(r, from, now)) tx(r, from, e, sizeof e); return;
            }
        }
        // Rate-limit expensive public-key operations after cookies, replay, source and capacity checks.
        roam_refill(&r->signature_budget, now, r->policy.signature_rate, r->policy.signature_burst);
        if (!roam_take(&r->signature_budget, 1)) { r->st.auth_limited++; return; }
        r->st.signature_checks++;
        if (!roam_auth_verify(b[2], p, p+ROAM_HELLO_BASE, p+ROAM_HELLO_BASE+21, p[ROAM_HELLO_BASE+20])) {
            r->st.auth_rejected++; return;
        }
        if (roam_moderation_banned(r->moderation, identity, roam_moderation_utc())) {
            r->st.ban_rejected++; banned_reply(r, from, now);
            if (k) drop_client(r, k); return;
        }
        if (used_slot >= 0) {
            memcpy(r->proofs[used_slot].digest, proof, 32);
            r->proofs[used_slot].expiry = r->absolute_ms + ROAM_COOKIE_MS + 1;
        }
        if (!k) {
            Source *src = source(r, from, now);
            int slot = r->free_head; k = &r->c[slot]; r->free_head = k->free_next;
            memset(k, 0, sizeof *k); k->used = 1; k->addr = *from;
            while (!r->next_id || r->ids[r->next_id]) r->next_id++;
            k->id = r->next_id++; r->ids[k->id] = slot + 1;
            unsigned bucket = ahash(from); k->anext = r->ahead[bucket]; r->ahead[bucket] = slot;
            k->tok_pkt = PKT_BURST; k->tok_byte = BYTE_BURST; k->tok_ms = now;
            r->st.clients++;
            // Only successful new admission extends retention; retries cannot pin a record.
            src->last = now;
        } else if (k->room != room) {
            // A room transition cannot keep a state, pins or neighbours from the old room.
            k->has_state = k->has_look = k->npins = k->nnb = 0;
        }
        memcpy(k->identity, identity, 32); memcpy(k->proof, proof, 32);
        k->last_rx = now; k->room = room; roam_name_copy(k->name, (const char *)p + 4);
        uint8_t w[ROAM_HDR + 4]; roam_hdr_put(w, ROAM_WELCOME, 0, k->txseq++);
        w[ROAM_HDR] = (uint8_t)k->id; w[ROAM_HDR + 1] = (uint8_t)(k->id >> 8);
        w[ROAM_HDR + 2] = (uint8_t)r->near; w[ROAM_HDR + 3] = ROAM_HZ;
        tx(r, from, w, sizeof w); return;
    }
    case ROAM_STATE: {
        if (!k || len != ROAM_STATE_BYTES) { r->st.rx_bad++; return; }
        if (k->has_state && (int16_t)(uint16_t)(seq - k->seq) <= 0) return;
        RoamState state; roam_state_get(p, &state);
        if (!roam_pose_valid(state.pos, state.vel)) { r->st.rx_bad++; return; }
        k->st = state; k->seq = seq; k->has_state = 1;
        k->state_ms = k->last_rx = now; return;
    }
    case ROAM_LOOK:
        if (!k || roam_look_get(p, len, &k->look)) { r->st.rx_bad++; return; }
        k->has_look = 1; k->last_rx = now; return;
    case ROAM_PINS:
        if (!k || len < 1 || p[0] > ROAM_PIN_MAX || len != 1 + p[0] * 2) { r->st.rx_bad++; return; }
        k->npins = 0;
        for (int i = 0; i < p[0]; ++i) {
            uint16_t pin = (uint16_t)(p[1 + i * 2] | p[2 + i * 2] << 8);
            int duplicate = pin == k->id || !pin;
            for (int j = 0; j < k->npins; ++j) if (k->pins[j] == pin) duplicate = 1;
            if (!duplicate) k->pins[k->npins++] = pin;
        }
        k->last_rx = now; return;
    case ROAM_BYE:
        if (k && !len) drop_client(r, k);
        else r->st.rx_bad++;
        return;
    default: r->st.rx_bad++;
    }
}
static int fresh(const Client *k, uint32_t now) {
    return k && k->used && k->has_state && (uint32_t)(now - k->state_ms) <= ROAM_STATE_FRESH_MS;
}
static int compare(RoamRelay *r, int ia, int ib, int axis) {
    Client *a = &r->c[ia], *b = &r->c[ib];
    if (axis == 3) { if (a->room != b->room) return a->room < b->room ? -1 : 1; }
    else if (a->st.pos[axis] != b->st.pos[axis]) return a->st.pos[axis] < b->st.pos[axis] ? -1 : 1;
    return a->id < b->id ? -1 : a->id > b->id;
}
static void median(RoamRelay *r, int lo, int hi, int mid, int axis) {
    while (lo < hi) {
        int i = lo, j = hi, pivot = r->order[(lo + hi) / 2];
        while (i <= j) {
            while (compare(r, r->order[i], pivot, axis) < 0) i++;
            while (compare(r, r->order[j], pivot, axis) > 0) j--;
            if (i <= j) { int t = r->order[i]; r->order[i++] = r->order[j]; r->order[j--] = t; }
        }
        if (mid <= j) hi = j; else if (mid >= i) lo = i; else break;
    }
}
static int subdivide(RoamRelay *r, int start, int count) {
    int index = r->ncells++; Cell *c = &r->cells[index];
    c->start = start; c->count = count; c->left = c->right = -1;
    c->room_lo = UINT32_MAX; c->room_hi = 0; c->min_id = UINT16_MAX;
    for (int a = 0; a < 3; ++a) { c->lo[a] = INFINITY; c->hi[a] = -INFINITY; }
    for (int j = start; j < start + count; ++j) {
        Client *k = &r->c[r->order[j]];
        if (k->room < c->room_lo) c->room_lo = k->room;
        if (k->room > c->room_hi) c->room_hi = k->room;
        if (k->id < c->min_id) c->min_id = k->id;
        for (int a = 0; a < 3; ++a) {
            float p = k->st.pos[a], future = p + k->st.vel[a] * 0.5f;
            c->lo[a] = fminf(c->lo[a], fminf(p, future)); c->hi[a] = fmaxf(c->hi[a], fmaxf(p, future));
        }
    }
    if (count > LEAF_MAX) {
        int axis = 0;
        for (int a = 1; a < 3; ++a) if (c->hi[a] - c->lo[a] > c->hi[axis] - c->lo[axis]) axis = a;
        if (c->room_lo != c->room_hi) axis = 3;
        int half = count / 2; median(r, start, start + count - 1, start + half, axis);
        c->left = subdivide(r, start, half); c->right = subdivide(r, start + half, count - half);
    }
    return index;
}
typedef struct { uint16_t id; float score, distance; int sticky, pin; } Choice;
typedef struct { RoamRelay *r; Client *self; Choice best[ROAM_INTEREST_MAX]; int n; uint32_t now; } Search;
static int old_index(const Client *k, uint16_t id) {
    for (int j = 0; j < k->nnb; ++j) if (k->nb[j].id == id) return j;
    return -1;
}
static int better(const Choice *a, const Choice *b) {
    if (a->score != b->score) return a->score < b->score;
    if (a->sticky != b->sticky) return a->sticky > b->sticky;
    return a->id < b->id;
}
static void consider(Search *s, Client *o, int pin, int sticky) {
    Client *k = s->self;
    if (!fresh(o, s->now) || o == k || o->room != k->room) return;
    for (int j = 0; j < s->n; ++j) if (s->best[j].id == o->id) return;
    float d = 0, future = 0;
    for (int a = 0; a < 3; ++a) {
        float p = o->st.pos[a] - k->st.pos[a], v = (o->st.vel[a] - k->st.vel[a]) * 0.5f;
        d += p * p; future += (p + v) * (p + v);
    }
    float distance = fminf(d, future);
    float limit = sticky && !ROAM_NO_HYSTERESIS ? 2200 : 2000;
    if (!pin && distance > limit * limit) return;
    Choice b = {.id=o->id, .distance=distance, .pin=pin, .sticky=sticky && !ROAM_NO_HYSTERESIS};
    b.score = pin ? -1.0f : distance * (b.sticky ? 0.81f : 1.0f);
    int at = s->n;
    if (at == s->r->near) { if (!better(&b, &s->best[at - 1])) return; at--; }
    else s->n++;
    while (at && better(&b, &s->best[at - 1])) { s->best[at] = s->best[at - 1]; at--; }
    s->best[at] = b;
}
static float cell_distance(const Cell *c, const Client *k) {
    float d = 0;
    for (int a = 0; a < 3; ++a) {
        float p = k->st.pos[a], future = p + k->st.vel[a] * 0.5f;
        float lo = fminf(p, future), hi = fmaxf(p, future);
        float e = lo > c->hi[a] ? lo - c->hi[a] : hi < c->lo[a] ? c->lo[a] - hi : 0;
        d += e * e;
    }
    return d;
}
static void search_cell(Search *s, int index) {
    if (index < 0) return;
    Cell *c = &s->r->cells[index]; Client *k = s->self;
    if (k->room < c->room_lo || k->room > c->room_hi) return;
    float d = cell_distance(c, k);
    if (d > 2000.0f * 2000.0f) return;
    if (s->n == s->r->near) {
        Choice *last = &s->best[s->n - 1];
        if (d > last->score || (d == last->score && (last->sticky || c->min_id >= last->id))) return;
    }
    if (c->left < 0) {
        for (int j = c->start; j < c->start + c->count; ++j) {
            Client *o = &s->r->c[s->r->order[j]]; s->r->st.candidates++;
            // Incumbents and pins were inserted before spatial search.
            if (old_index(k, o->id) < 0) consider(s, o, 0, 0);
        }
    } else {
        int a = c->left, b = c->right;
        float da = cell_distance(&s->r->cells[a], k), db = cell_distance(&s->r->cells[b], k);
        if (db < da || (db == da && s->r->cells[b].min_id < s->r->cells[a].min_id)) { int t = a; a = b; b = t; }
        search_cell(s, a); search_cell(s, b);
    }
}
static void select_interests(RoamRelay *r, Client *k, uint32_t now) {
    Search s = {.r=r, .self=k, .now=now};
    if (fresh(k, now)) {
        for (int j = 0; j < k->npins; ++j) consider(&s, by_id(r, k->pins[j]), 1, old_index(k, k->pins[j]) >= 0);
        for (int j = 0; j < k->nnb; ++j) consider(&s, by_id(r, k->nb[j].id), 0, 1);
        search_cell(&s, r->root);
    }
    Interest nb[ROAM_INTEREST_MAX]; int counts[3] = {0}, kept = 0;
    for (int j = 0; j < s.n; ++j) {
        Choice *b = &s.best[j]; int old = old_index(k, b->id);
        int tier = 2;
        float near = !ROAM_NO_HYSTERESIS && old >= 0 && k->nb[old].tier == 0 ? 115 : 100;
        float mid = !ROAM_NO_HYSTERESIS && old >= 0 && k->nb[old].tier <= 1 ? 340 : 300;
        if (b->distance <= near * near && counts[0] < 8) tier = 0;
        else if (b->distance <= mid * mid && counts[1] < 8) tier = 1;
        // The 16 map slots remain bounded even when all higher tiers are empty.
        if (tier == 2 && counts[2] == 16) continue;
        counts[tier]++;
        nb[kept] = old >= 0 ? k->nb[old] : (Interest){.id=b->id};
        if (old < 0) r->st.interest_changes++;
        else if (nb[kept].tier != tier) r->st.tier_changes++;
        nb[kept++].tier = (uint8_t)tier;
    }
    for (int j = 0; j < k->nnb; ++j) {
        int found = 0; for (int q = 0; q < kept; ++q) if (nb[q].id == k->nb[j].id) found = 1;
        if (!found) r->st.interest_changes++;
    }
    memcpy(k->nb, nb, (size_t)kept * sizeof nb[0]); k->nnb = kept;
    r->st.selections++;
    k->members_pending = 1;
}
static void send_members(RoamRelay *r, Client *k) {
    if (!k->members_pending) return;
    // Full, sequenced membership snapshots repair loss and prune old buffers immediately.
    uint8_t pkt[ROAM_HDR + 1 + ROAM_INTEREST_MAX * 5];
    int m = roam_hdr_put(pkt, ROAM_MEMBERS, 0, k->member_seq++); pkt[m++] = (uint8_t)k->nnb;
    for (int j = 0; j < k->nnb; ++j) {
        pkt[m++] = (uint8_t)k->nb[j].id; pkt[m++] = (uint8_t)(k->nb[j].id >> 8); pkt[m++] = k->nb[j].tier;
        Client *o = by_id(r, k->nb[j].id);
        uint16_t state_seq = o ? o->seq : 0;
        pkt[m++] = (uint8_t)state_seq; pkt[m++] = (uint8_t)(state_seq >> 8);
    }
    if (tx(r, &k->addr, pkt, m)) k->members_pending = 0;
}
static void send_control(RoamRelay *r, Client *k, uint32_t now) {
    // One control datagram per client/tick; alternate priority under constrained output.
    for (int off = 0; off < k->nnb; ++off) {
        int j = (k->look_cursor + off) % k->nnb;
        Interest *nb = &k->nb[j]; Client *o = by_id(r, nb->id);
        if (!fresh(o, now) || o->room != k->room) continue;
        if (!nb->name_ms || (uint32_t)(now - nb->name_ms) >= LOOK_RESEND_MS) {
            uint8_t name[ROAM_HDR + 16]; int len = roam_hdr_put(name, ROAM_NAME, o->id, k->txseq++);
            memcpy(name + len, o->name, 16); len += 16;
            if (tx(r, &k->addr, name, len)) nb->name_ms = now;
            k->look_cursor = (j + 1) % k->nnb; break;
        }
        if (!o->has_look) continue;
        if (nb->look_rev == o->look.rev && nb->look_ms && (uint32_t)(now - nb->look_ms) < LOOK_RESEND_MS) continue;
        uint8_t look[ROAM_MAX_DGRAM]; int len = roam_hdr_put(look, ROAM_LOOK, o->id, k->txseq++);
        len += roam_look_put(look + len, &o->look);
        if (tx(r, &k->addr, look, len)) { nb->look_rev = o->look.rev; nb->look_ms = now; }
        k->look_cursor = (j + 1) % k->nnb; break;
    }
}
void roam_relay_tick(RoamRelay *r, uint32_t now) {
    uint64_t start = clock_us();
    now = relay_time(r, now);
    (void)cookie_keys(r);
    expire_sources(r, now);
    // Refresh idle global buckets too, so long quiet periods do not cross the modular half-range.
    roam_refill(&r->admissions, now, r->policy.admission_rate, r->policy.admission_burst);
    roam_refill(&r->challenges, now, r->policy.challenge_rate, r->policy.challenge_burst);
    roam_refill(&r->ingress_packets, now, r->policy.ingress_packets, r->policy.ingress_packet_burst);
    roam_refill(&r->ingress_bytes, now, r->policy.ingress_bytes, r->policy.ingress_byte_burst);
    roam_refill(&r->egress_packets, now, r->policy.egress_packets, r->policy.egress_packet_burst);
    roam_refill(&r->egress_bytes, now, r->policy.egress_bytes, r->policy.egress_byte_burst);
    for (int i = 0; i < r->max; ++i)
        if (r->c[i].used && (uint32_t)(now - r->c[i].last_rx) > TIMEOUT_MS) { drop_client(r, &r->c[i]); r->st.timeouts++; }
    if (!r->selected || (uint32_t)(now - r->last_select) >= ROAM_INTEREST_MS) {
        int n = 0; r->ncells = 0;
        for (int i = 0; i < r->max; ++i) if (fresh(&r->c[i], now)) r->order[n++] = i;
        r->root = n ? subdivide(r, 0, n) : -1;
        for (int off = 0; off < r->max; ++off) {
            int i = (int)((r->output_cursor + (unsigned)off) % (unsigned)r->max);
            if (r->c[i].used) select_interests(r, &r->c[i], now);
        }
        r->last_select = now; r->selected = 1;
    }
    uint8_t pkt[ROAM_MAX_DGRAM];
    unsigned next_cursor = (r->output_cursor + 1) % (unsigned)r->max;
    int constrained = 0;
    for (int off = 0; off < r->max; ++off) {
        int i = (int)((r->output_cursor + (unsigned)off) % (unsigned)r->max);
        Client *k = &r->c[i]; if (!k->used) continue;
        uint64_t suppressed = r->st.tx_suppressed, sent_before = r->st.tx;
        int turn = k->service_turn;
        int m = roam_hdr_put(pkt, ROAM_BUNDLE, 0, k->txseq++), count = 0, rank[3] = {0}; pkt[m++] = 0;
        if (turn == 0) send_members(r, k);
        if (turn == 1 && fresh(k, now)) send_control(r, k, now);
        for (int j = 0; j < k->nnb; ++j) {
            Interest *nb = &k->nb[j]; Client *o = by_id(r, nb->id); int tier = nb->tier;
            int phase = ROAM_NO_STAGGER ? 0 : rank[tier]++;
            int period = tier == 0 ? 1 : tier == 1 ? 2 : 10;
            if (!fresh(o, now) || o->room != k->room) continue;
            if ((r->tick + k->id) % (unsigned)period != (unsigned)phase % (unsigned)period) continue;
            if (count == ROAM_BUNDLE_MAX) { r->st.bundle_overflow++; continue; }
            pkt[m] = (uint8_t)o->id; pkt[m+1] = (uint8_t)(o->id >> 8);
            pkt[m+2] = (uint8_t)o->seq; pkt[m+3] = (uint8_t)(o->seq >> 8);
            roam_state_put(pkt + m + 4, &o->st); m += ROAM_ENTRY_BYTES; count++;
            r->st.state_entries[tier]++;
        }
        pkt[ROAM_HDR] = (uint8_t)count;
        if (count && fresh(k, now)) tx(r, &k->addr, pkt, m);
        if (turn != 1 && fresh(k, now)) send_control(r, k, now);
        if (turn != 0) send_members(r, k);
        if (r->st.tx != sent_before) k->service_turn = (turn + 1) % 3;
        if (!constrained && r->st.tx_suppressed != suppressed) {
            next_cursor = (unsigned)(i + 1) % (unsigned)r->max; constrained = 1;
        }
    }
    r->output_cursor = next_cursor;
    r->last_tick = now; r->ticked = 1; r->tick++; r->st.ticks++;
    uint64_t elapsed = clock_us() - start; if (elapsed > r->st.max_tick_us) r->st.max_tick_us = elapsed;
}
static uint64_t service_us(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000 + (uint64_t)ts.tv_nsec / 1000;
}
int roam_relay_service(RoamRelay *r, uint32_t now) {
    int got = 0;
    uint64_t started = service_us();
    if (!r->ticked || (int32_t)(now - r->last_tick) >= 1000 / ROAM_HZ) roam_relay_tick(r, now);
    for (int budget = 0; budget < 4096; ++budget) {
        uint64_t elapsed = service_us() - started;
        if (elapsed >= 2000) { r->st.receive_yields++; break; }
        uint8_t b[ROAM_MAX_DGRAM + 1]; struct sockaddr_in from; socklen_t fl = sizeof from;
        ssize_t n = recvfrom(r->fd, b, sizeof b, MSG_DONTWAIT, (struct sockaddr *)&from, &fl);
        if (n < 0) { if (errno == EINTR) continue; break; }
        if (fl != sizeof from || from.sin_family != AF_INET) { r->st.rx_bad++; continue; }
        roam_relay_input(r, b, (int)n, &from, now + (uint32_t)(elapsed / 1000)); got++;
    }
    uint32_t end = now + (uint32_t)((service_us() - started) / 1000);
    if (!r->ticked || (int32_t)(end - r->last_tick) >= 1000 / ROAM_HZ) roam_relay_tick(r, end);
    return got;
}

void roam_relay_moderation(RoamRelay *r, RoamModeration *m) { r->moderation = m; roam_relay_enforce_bans(r); }
void roam_relay_enforce_bans(RoamRelay *r) {
    uint64_t now = roam_moderation_utc();
    for (int i = 0; i < r->max; ++i) if (r->c[i].used && roam_moderation_banned(r->moderation, r->c[i].identity, now)) {
        banned_reply(r, &r->c[i].addr, r->now);
        drop_client(r, &r->c[i]); r->st.ban_rejected++;
    }
}
int roam_relay_identity(RoamRelay *r, uint16_t session, uint8_t id[32]) {
    Client *c = by_id(r, session); if (!c) return 0;
    memcpy(id, c->identity, 32); return 1;
}
int roam_relay_kick(RoamRelay *r, uint16_t session) {
    Client *c = by_id(r, session); if (!c) return 0;
    drop_client(r, c); r->st.kicks++; return 1;
}
int roam_relay_kick_identity(RoamRelay *r, const uint8_t id[32]) {
    int removed = 0;
    for (int i = 0; i < r->max; ++i) if (r->c[i].used && !memcmp(r->c[i].identity, id, 32)) {
        drop_client(r, &r->c[i]); r->st.kicks++; removed++;
    }
    return removed;
}
int roam_relay_list(RoamRelay *r, uint16_t after, char *out, size_t cap) {
    size_t used = 0; unsigned last = after; int count = 0;
    for (unsigned i = (unsigned)after+1; i < 65536 && count < 32; ++i) {
        Client *c = by_id(r, (uint16_t)i); if (!c) continue;
        char hex[65]; roam_auth_hex(c->identity, hex);
        int n = snprintf(out+used, cap-used, "%u %s\n", i, hex);
        if (n < 0 || (size_t)n >= cap-used) break;
        used += (size_t)n; last = i; count++;
    }
    int n = snprintf(out+used, cap-used, "END %u\n", count == 32 ? last : 0);
    return n > 0 && (size_t)n < cap-used ? (int)(used+(size_t)n) : (int)used;
}
