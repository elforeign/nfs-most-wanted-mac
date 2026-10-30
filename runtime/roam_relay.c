// UDP relay: hashed endpoints, 5 Hz spatial selection, 32 bounded interests, tiered snapshots.
#include "roam_core.h"
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
#define ERRMEMO 64
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
    int has_state, has_look;
    RoamState st;
    RoamLook look;
    float tok_pkt, tok_byte;
    Interest nb[ROAM_INTEREST_MAX];
    int nnb, look_cursor;
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
struct RoamRelay {
    int fd, near, max, free_head;
    Client *c;
    int ahead[HBUCKETS], *ids, *order;
    Cell *cells;
    int ncells, root;
    uint32_t last_tick, last_select, tick;
    int ticked, selected;
    struct sockaddr_in errmemo[ERRMEMO];
    int nerr;
    unsigned errnext;
    RoamRelayStats st;
    uint16_t next_id;
};
static uint64_t clock_us(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000 + (uint64_t)t.tv_nsec / 1000;
}
static int same(const struct sockaddr_in *a, const struct sockaddr_in *b) {
    return a->sin_addr.s_addr == b->sin_addr.s_addr && a->sin_port == b->sin_port;
}
static unsigned ahash(const struct sockaddr_in *a) {
    uint32_t h = a->sin_addr.s_addr ^ ((uint32_t)a->sin_port * 0x9e3779b9u);
    h ^= h >> 16; h *= 0x85ebca6bu; h ^= h >> 13;
    return h & (HBUCKETS - 1);
}
RoamRelay *roam_relay_new(int fd, int near, int max_clients) {
    RoamRelay *r = calloc(1, sizeof *r);
    if (!r) return NULL;
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
    ssize_t sent = r->fd < 0 ? n : sendto(r->fd, b, (size_t)n, MSG_DONTWAIT, (const struct sockaddr *)to, sizeof *to);
    if (sent == n) { r->st.tx++; r->st.tx_bytes += (uint64_t)n; return 1; }
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
void roam_relay_input(RoamRelay *r, const uint8_t *b, int n, const struct sockaddr_in *from, uint32_t now) {
    int type; uint16_t id, seq;
    r->st.rx++; if (n > 0) r->st.rx_bytes += (uint64_t)n;
    int h = roam_hdr_get(b, n, &type, &id, &seq);
    if (h == -1 || n > ROAM_MAX_DGRAM) { r->st.rx_bad++; return; }
    if (h == -2) {
        r->st.rx_version++;
        for (int i = 0; i < r->nerr; ++i) if (same(&r->errmemo[i], from)) return;
        r->errmemo[r->errnext++ % ERRMEMO] = *from;
        if (r->nerr < ERRMEMO) r->nerr++;
        uint8_t e[ROAM_HDR + 1]; roam_hdr_put(e, ROAM_ERR, 0, 0); e[ROAM_HDR] = ROAM_ERR_VERSION;
        tx(r, from, e, sizeof e); return;
    }
    Client *k = by_addr(r, from);
    if (k) {
        float dt = (float)(uint32_t)(now - k->tok_ms) / 1000.0f;
        k->tok_ms = now;
        k->tok_pkt = fminf(PKT_BURST, k->tok_pkt + dt * PKT_RATE);
        k->tok_byte = fminf(BYTE_BURST, k->tok_byte + dt * BYTE_RATE);
        if (k->tok_pkt < 1 || k->tok_byte < (float)n) { r->st.rx_rate_dropped++; return; }
        k->tok_pkt -= 1; k->tok_byte -= (float)n;
        if (type != ROAM_HELLO && id != k->id) { r->st.rx_bad++; return; }
    }
    const uint8_t *p = b + ROAM_HDR; int len = n - ROAM_HDR;
    switch (type) {
    case ROAM_HELLO: {
        if (len != 20) { r->st.rx_bad++; return; }
        uint32_t room = (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
        if (!k) {
            if (r->free_head < 0) {
                r->st.full++; uint8_t e[ROAM_HDR + 1];
                roam_hdr_put(e, ROAM_ERR, 0, 0); e[ROAM_HDR] = ROAM_ERR_FULL; tx(r, from, e, sizeof e); return;
            }
            int slot = r->free_head; k = &r->c[slot]; r->free_head = k->free_next;
            memset(k, 0, sizeof *k); k->used = 1; k->addr = *from;
            while (!r->next_id || r->ids[r->next_id]) r->next_id++;
            k->id = r->next_id++; r->ids[k->id] = slot + 1;
            unsigned bucket = ahash(from); k->anext = r->ahead[bucket]; r->ahead[bucket] = slot;
            k->tok_pkt = PKT_BURST; k->tok_byte = BYTE_BURST; k->tok_ms = now;
            r->st.clients++;
        } else if (k->room != room) {
            // A room transition cannot keep a state, pins or neighbours from the old room.
            k->has_state = k->has_look = k->npins = k->nnb = 0;
        }
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
    // Full, sequenced membership snapshots repair loss and prune old buffers immediately.
    uint8_t pkt[ROAM_HDR + 1 + ROAM_INTEREST_MAX * 5];
    int m = roam_hdr_put(pkt, ROAM_MEMBERS, 0, k->member_seq++); pkt[m++] = (uint8_t)k->nnb;
    for (int j = 0; j < k->nnb; ++j) {
        pkt[m++] = (uint8_t)k->nb[j].id; pkt[m++] = (uint8_t)(k->nb[j].id >> 8); pkt[m++] = k->nb[j].tier;
        Client *o = by_id(r, k->nb[j].id);
        pkt[m++] = (uint8_t)o->seq; pkt[m++] = (uint8_t)(o->seq >> 8);
    }
    tx(r, &k->addr, pkt, m);
}
void roam_relay_tick(RoamRelay *r, uint32_t now) {
    uint64_t start = clock_us();
    for (int i = 0; i < r->max; ++i)
        if (r->c[i].used && (uint32_t)(now - r->c[i].last_rx) > TIMEOUT_MS) { drop_client(r, &r->c[i]); r->st.timeouts++; }
    if (!r->selected || (uint32_t)(now - r->last_select) >= ROAM_INTEREST_MS) {
        int n = 0; r->ncells = 0;
        for (int i = 0; i < r->max; ++i) if (fresh(&r->c[i], now)) r->order[n++] = i;
        r->root = n ? subdivide(r, 0, n) : -1;
        for (int i = 0; i < r->max; ++i) if (r->c[i].used) select_interests(r, &r->c[i], now);
        r->last_select = now; r->selected = 1;
    }
    uint8_t pkt[ROAM_MAX_DGRAM];
    for (int i = 0; i < r->max; ++i) {
        Client *k = &r->c[i]; if (!k->used || !fresh(k, now)) continue;
        int m = roam_hdr_put(pkt, ROAM_BUNDLE, 0, k->txseq++), count = 0, rank[3] = {0}; pkt[m++] = 0;
        // LOOK/control have a separate one-datagram/client/tick budget and do not displace states.
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
        if (count) tx(r, &k->addr, pkt, m);
    }
    r->last_tick = now; r->ticked = 1; r->tick++; r->st.ticks++;
    uint64_t elapsed = clock_us() - start; if (elapsed > r->st.max_tick_us) r->st.max_tick_us = elapsed;
}
int roam_relay_service(RoamRelay *r, uint32_t now) {
    int got = 0;
    for (int budget = 0; budget < 4096; ++budget) {
        uint8_t b[ROAM_MAX_DGRAM + 1]; struct sockaddr_in from; socklen_t fl = sizeof from;
        ssize_t n = recvfrom(r->fd, b, sizeof b, MSG_DONTWAIT, (struct sockaddr *)&from, &fl);
        if (n < 0) { if (errno == EINTR) continue; break; }
        roam_relay_input(r, b, (int)n, &from, now); got++;
    }
    if (!r->ticked || (uint32_t)(now - r->last_tick) >= 1000 / ROAM_HZ) roam_relay_tick(r, now);
    return got;
}
